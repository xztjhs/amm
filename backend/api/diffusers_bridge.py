"""
Diffusers Bridge API
====================
内置桥接，为文生图(T2I)/文生视频(T2V)/图生视频(I2V)提供推理接口，不走子进程。
启动时按需加载 pipeline，首次推理延迟较高。

支持的 Pipeline:
  - T2I: QwenImagePipeline (Qwen-Image-2512)
  - T2V: WanPipeline        (Wan2.2-T2V-A14B)
  - I2V: WanPipeline        (Wan2.2-I2V-A14B)，通过传入 image= 首帧图
"""
import asyncio
import base64
import io
import json
import logging
import os
import glob
from pathlib import Path
from typing import Dict, Any, Optional, List

from aiohttp import web

logger = logging.getLogger("AMM.DiffusersBridge")

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
MODELSCOPE_CACHE = os.environ.get("MODELSCOPE_CACHE", os.path.join(MODELS_DIR, "zoo", "modelscope"))

# 惰性加载的 pipeline 缓存
_pipeline_cache: Dict[str, Any] = {}

# ============================================================
# Quantization / dtype helpers (CUDA 13 + Blackwell sm_120)
# ============================================================

# 老板定的铁律 (2026-08-05):
# - 推理引擎优先级: vllm > llama.cpp > Diffusers
# - 每类模型部署/测试/验证只开 1 个、依次串行,避免 84GB 显存不够
# - vLLM 实例 (PID 27048) 占用 ~57G → Diffusers 文生视频必须腾地方
#
# Wan2.2-T2V-A14B: 27B MoE (14B high + 14B low 专家),  BF16 静态 ~75G → OOM
# 解决方案: enable_layerwise_casting(storage=fp8_e4m3fn, compute=bf16)
#   - 权重存储按 FP8 → 两个 transformer 从 56G 降到 ~28G
#   - 实时计算按 BF16 → 质量几乎无损
#   - WanTransformer3DModel 已声明 _skip_layerwise_casting_patterns 和 _keep_in_fp32_modules

def _torch_dtype(name: Optional[str]):
    """把字符串 dtype 映射到 torch.dtype"""
    import torch
    if not name:
        return None
    n = name.lower()
    mapping = {
        "fp8": torch.float8_e4m3fn,
        "float8": torch.float8_e4m3fn,
        "float8_e4m3fn": torch.float8_e4m3fn,
        "bf16": torch.bfloat16,
        "bfloat16": torch.bfloat16,
        "fp16": torch.float16,
        "float16": torch.float16,
        "fp32": torch.float32,
        "float32": torch.float32,
    }
    return mapping.get(n)


def _should_enable_layerwise_casting(model_cfg: Dict) -> bool:
    """决定是否对 transformer 启用 FP8 存储 + BF16 计算

    触发逻辑 (优先级从高到低):
      1. model_cfg["quant"] == "none"  → 强制关闭 (用户在 YAML 明确要 BF16)
      2. model_cfg["quant"] == "fp8"   → 强制开启 (用户明确要 FP8)
      3. model_cfg["quant"] == "bf16"  → 强制关闭 (明确 BF16)
      4. model_cfg["quant"] 为空 / 未设:
           - category=video AND model_id 含 A14B → 开启 (兜底, MoE 27B 必备)
           - 其他 → 关闭 (Qwen-Image 等 8-20B 走 BF16)
    """
    quant_raw = model_cfg.get("quant")
    quant = (quant_raw or "").lower().strip() if quant_raw else ""
    if quant in ("none", "off", "bf16", "fp16", "fp32"):
        return False
    if quant in ("fp8", "float8", "float8_e4m3fn"):
        return True
    # 默认: 视频 MoE (Wan2.2-A14B / I2V-A14B) 开启
    if model_cfg.get("category") == "video" and "A14B" in model_cfg.get("model_id", ""):
        return True
    return False


def _apply_layerwise_casting(pipe) -> None:
    """对 Wan / QwenImage 等 pipeline 的 transformer 应用 FP8 layerwise casting

    - 两个 transformer (高噪 + 低噪专家) 都要分别 enable
    - VAE 保留 FP32 (解码器质量敏感,文件也不大)
    - text_encoder 通常不大,保留 BF16
    """
    for attr in ("transformer", "transformer_2"):
        mod = getattr(pipe, attr, None)
        if mod is None:
            continue
        try:
            # storage=FP8 (权重驻留), compute=BF16 (前向计算)
            mod.enable_layerwise_casting(
                storage_dtype=_torch_dtype("fp8") or getattr(__import__("torch"), "float8_e4m3fn"),
                compute_dtype=_torch_dtype("bf16"),
            )
            logger.info(f"layerwise_casting enabled on {attr}: storage=fp8, compute=bf16")
        except AttributeError:
            logger.warning(f"{attr} 不支持 enable_layerwise_casting (旧版 diffusers?), 跳过")
        except Exception as e:
            logger.exception(f"{attr} enable_layerwise_casting 失败: {e}")


def _apply_group_offload(pipe, onload_device=None, offload_to_cpu: bool = True) -> None:
    """显存仍不够时的后备方案: leaf_level CPU offload

    触发条件:
      - model_cfg["cpu_offload"] == True
      - 或者 FP8 后实测仍 OOM (由调用方决定)
    """
    if not offload_to_cpu:
        return
    import torch as _t
    onload = onload_device or (_t.device("cuda") if _t.cuda.is_available() else _t.device("cpu"))
    offload = _t.device("cpu")

    for attr in ("transformer", "transformer_2", "text_encoder"):
        mod = getattr(pipe, attr, None)
        if mod is None:
            continue
        try:
            mod.enable_group_offload(
                onload_device=onload,
                offload_device=offload,
                offload_type="leaf_level",
                use_stream=True,
            )
            logger.info(f"group_offload enabled on {attr}: leaf_level, stream")
        except Exception as e:
            logger.warning(f"{attr} enable_group_offload 失败: {e}")

# ============================================================
# Path / Config helpers
# ============================================================

def _resolve_local_path(model_id: str) -> str:
    """从 ModelScope cache 解析模型本地路径 (snapshot/master)"""
    # cache 布局: <cache>/models/<org>--<name>/snapshots/<revision>/...
    org_name = model_id.replace("/", "--")
    cand = str(Path(MODELSCOPE_CACHE) / "models" / org_name / "snapshots" / "master")
    if os.path.isdir(cand):
        return cand
    # 兼容旧布局 <cache>/<org>--<name>/...
    cand2 = str(Path(MODELSCOPE_CACHE) / org_name)
    if os.path.isdir(cand2):
        return cand2
    # 兼容直接给定本地路径
    if os.path.isdir(model_id):
        return model_id
    raise FileNotFoundError(f"Model 本地路径未找到: {model_id} (cache={MODELSCOPE_CACHE})")


def _find_model_cfg_by_category(manager, category: str) -> Optional[Dict]:
    """按 category 查找模型配置 (t2i->image, t2v/i2v->video)"""
    for key in manager._model_keys():
        cfg = manager.config.get(key)
        if cfg and cfg.get("category") == category and cfg.get("id") in ("t2i", "t2v", "i2v"):
            return cfg
    return None


def _find_model_cfg(manager, model_id: str) -> Optional[Dict]:
    return manager._find_model_config(model_id)


# ============================================================
# Pipeline loading
# ============================================================

async def _load_pipeline(model_cfg: Dict) -> Any:
    """异步加载 diffusers pipeline (独立线程执行，阻塞式)

    关键能力 (2026-08-05 增强):
      - 支持 Wan2.2 MoE 双专家 (transformer + transformer_2 一起加载)
      - 支持 FP8 layerwise_casting 量化加载 (transformer 权重存储 FP8、计算 BF16)
      - 支持 group_offload 后备方案 (显存仍不够时)
      - 保留 enable_model_cpu_offload 作为最后兜底
    """
    model_id = model_cfg.get("model_id", "")
    category = model_cfg.get("category", "")
    model_source = model_cfg.get("model_source", "modelscope")

    cache_key = f"{category}:{model_id}:quant={model_cfg.get('quant','default')}"
    if cache_key in _pipeline_cache:
        return _pipeline_cache[cache_key]

    logger.info(f"Loading {category} pipeline: {model_id} (source={model_source}, quant={model_cfg.get('quant','default')})")

    def _load():
        import torch
        import json as _json

        local = _resolve_local_path(model_id)
        logger.info(f"Local model path: {local}")

        # ---- 计算 / 存储 dtype ----
        # image 类 (Qwen-Image) 小 (54G 全 BF16 可吃下), 不强求 FP8
        # video 类 (Wan2.2-A14B) 27B MoE, 必须 FP8
        if category == "image":
            compute_dtype = _torch_dtype(model_cfg.get("compute_dtype", "bf16")) or torch.bfloat16
            storage_dtype = compute_dtype  # 不量化
        else:
            compute_dtype = _torch_dtype(model_cfg.get("compute_dtype", "bf16")) or torch.bfloat16
            storage_dtype = compute_dtype  # from_pretrained 默认加载用 compute_dtype
            # Wan VAE 解码器需要 FP32 保真
            vae_dtype = torch.float32

        if category == "image":
            from diffusers import QwenImagePipeline
            # 适配 ModelScope 下载的权重: 没有 .bf16 变体后缀, 不能传 variant
            # (HF 仓库才有 model.bf16.safetensors 这种, ModelScope 默认走 fp32 总分片)
            pipe = QwenImagePipeline.from_pretrained(
                local,
                torch_dtype=compute_dtype,
            )
        else:
            # 视频: 根据 model_index.json 的 _class_name 动态选择 pipeline
            from diffusers import WanPipeline, WanImageToVideoPipeline, WanImage2VideoModularPipeline
            pipe_cls = WanPipeline
            idx_path = os.path.join(local, "model_index.json")
            try:
                with open(idx_path) as f:
                    idx = _json.load(f)
                cls_name = idx.get("_class_name", "")
                mapping = {
                    "WanImageToVideoPipeline": WanImageToVideoPipeline,
                    "WanImage2VideoModularPipeline": WanImage2VideoModularPipeline,
                    "WanPipeline": WanPipeline,
                }
                if cls_name in mapping:
                    pipe_cls = mapping[cls_name]
                logger.info(f"video pipeline class: {cls_name or 'WanPipeline(default)'}")
            except Exception as e:
                logger.warning(f"read model_index failed, default WanPipeline: {e}")

            # Wan2.2 MoE 双专家: boundary_ratio 由 model_cfg 指定, 默认 0.875 (官方)
            boundary_ratio = model_cfg.get("boundary_ratio")
            if boundary_ratio is None and "Wan2.2" in model_id:
                boundary_ratio = 0.875  # 官方默认值
            logger.info(f"Wan MoE boundary_ratio = {boundary_ratio}")

            load_kwargs = dict(
                torch_dtype=compute_dtype,
            )
            # boundary_ratio 在 WanPipeline.__init__ 阶段传入 (写进 config)
            if boundary_ratio is not None:
                load_kwargs["boundary_ratio"] = float(boundary_ratio)

            pipe = pipe_cls.from_pretrained(local, **load_kwargs)

        # ---- FP8 layerwise_casting (transformer 权重降精度存储) ----
        if _should_enable_layerwise_casting(model_cfg):
            _apply_layerwise_casting(pipe)

        # ---- 显存搬运策略 ----
        # 优先级: group_offload (CPU leaf) > enable_model_cpu_offload > 全 GPU
        if model_cfg.get("cpu_offload"):
            _apply_group_offload(pipe, offload_to_cpu=True)
            # VAE 仍放 GPU,提升解码速度
            try:
                if torch.cuda.is_available() and getattr(pipe, "vae", None) is not None:
                    pipe.vae.to("cuda")
            except Exception as e:
                logger.warning(f"VAE 移回 GPU 失败: {e}")
        else:
            # 默认: 序列 CPU offload (保守且稳)
            try:
                pipe.enable_model_cpu_offload()
                logger.info("enable_model_cpu_offload enabled (default)")
            except Exception as e:
                logger.warning(f"enable_model_cpu_offload 失败, fallback cuda: {e}")
                if torch.cuda.is_available():
                    pipe = pipe.to("cuda")

        return pipe

    loop = asyncio.get_event_loop()
    try:
        pipe = await loop.run_in_executor(None, _load)
    except Exception as e:
        logger.exception(f"Pipeline load failed for {model_id}")
        raise
    _pipeline_cache[cache_key] = pipe
    logger.info(f"Pipeline loaded: {cache_key}")
    return pipe


def _to_base64_png(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _to_base64_video_bytes(raw_bytes: bytes) -> str:
    return base64.b64encode(raw_bytes).decode()


# ============================================================
# HTTP Handler
# ============================================================

class DiffusersBridgeHandler:
    def __init__(self, manager):
        self.manager = manager

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                  headers={"Access-Control-Allow-Origin": "*"})

    async def health(self, req):
        return self._json({"status": "ok", "bridge": "diffusers"})

    # ---- T2I: 文生图 ----
    async def t2i_generate(self, req):
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            if not prompt:
                return self._json({"error": "prompt required"}, 400)

            model_cfg = _find_model_cfg(self.manager, "t2i") or _find_model_cfg_by_category(self.manager, "image")
            if not model_cfg:
                return self._json({"error": "t2i 模型未配置"}, 500)
            pipe = await _load_pipeline(model_cfg)

            import torch
            width = int(data.get("width", 1024)) if data.get("width") else 1024
            height = int(data.get("height", 1024)) if data.get("height") else 1024
            num_steps = int(data.get("num_inference_steps", 28))
            true_cfg = float(data.get("guidance_scale", data.get("true_cfg_scale", 5.0)))
            seed = int(data.get("seed", -1))
            num_images = min(int(data.get("n", 1)), 4)

            generator = None
            if seed >= 0:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)

            def _gen():
                with torch.no_grad():
                    result = pipe(
                        prompt=prompt,
                        height=height,
                        width=width,
                        num_inference_steps=num_steps,
                        true_cfg_scale=true_cfg,
                        num_images_per_prompt=num_images,
                        generator=generator,
                    )
                if isinstance(result, tuple):
                    return list(result[0])
                return list(result.images) if hasattr(result, "images") else list(result)

            loop = asyncio.get_event_loop()
            images = await loop.run_in_executor(None, _gen)

            data_list = [{"b64_json": _to_base64_png(img)} for img in images]
            return self._json({"data": data_list, "created": int(asyncio.get_event_loop().time())})

        except Exception as e:
            logger.exception("t2i generate error")
            return self._json({"error": str(e)}, 500)

    # ---- T2V: 文生视频 ----
    async def t2v_generate(self, req):
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            if not prompt:
                return self._json({"error": "prompt required"}, 400)

            model_cfg = _find_model_cfg(self.manager, "t2v") or _find_model_cfg_by_category(self.manager, "video")
            if not model_cfg:
                return self._json({"error": "t2v 模型未配置"}, 500)
            pipe = await _load_pipeline(model_cfg)

            import torch
            from diffusers.utils import export_to_video

            res = data.get("resolution", "480p")
            height, width = (480, 832) if str(res).lower() == "480p" else (720, 1280)
            num_frames = int(data.get("num_frames", 81))
            num_steps = int(data.get("num_inference_steps", 50))
            guidance = float(data.get("guidance_scale", 5.0))
            # Wan2.2 MoE: 双 guidance, 官方默认值 4.0 / 3.0
            guidance_2 = data.get("guidance_scale_2")
            if guidance_2 is not None:
                guidance_2 = float(guidance_2)
            seed = int(data.get("seed", -1))
            fps = int(data.get("frame_rate", 16))

            generator = None
            if seed >= 0:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)

            def _gen():
                import inspect as _inspect
                pipe_kwargs = dict(
                    prompt=prompt,
                    height=height,
                    width=width,
                    num_frames=num_frames,
                    num_inference_steps=num_steps,
                    guidance_scale=guidance,
                    generator=generator,
                    output_type="np",
                )
                # Wan2.2 MoE 才需要 guidance_scale_2
                if guidance_2 is not None and getattr(pipe, "transformer_2", None) is not None:
                    sig = _inspect.signature(pipe.__call__)
                    if "guidance_scale_2" in sig.parameters:
                        pipe_kwargs["guidance_scale_2"] = guidance_2
                with torch.no_grad():
                    frames = pipe(**pipe_kwargs)
                # frames 可能是 (video, meta) 或 numpy 数组
                vid = frames[0] if isinstance(frames, (tuple, list)) else frames
                return export_to_video(vid, fps=fps)

            loop = asyncio.get_event_loop()
            mp4_bytes = await loop.run_in_executor(None, _gen)
            if isinstance(mp4_bytes, str):  # 返回文件路径
                with open(mp4_bytes, "rb") as f:
                    mp4_bytes = f.read()

            return self._json({
                "data": [{"b64_json": _to_base64_video_bytes(mp4_bytes), "mime": "video/mp4"}],
                "created": int(asyncio.get_event_loop().time()),
            })

        except Exception as e:
            logger.exception("t2v generate error")
            return self._json({"error": str(e)}, 500)

    # ---- I2V: 图生视频 ----
    async def i2v_generate(self, req):
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            image_b64 = data.get("image", data.get("image_b64", ""))
            if not image_b64:
                return self._json({"error": "image (base64) required"}, 400)

            model_cfg = _find_model_cfg(self.manager, "i2v") or _find_model_cfg_by_category(self.manager, "video")
            if not model_cfg:
                return self._json({"error": "i2v 模型未配置"}, 500)
            pipe = await _load_pipeline(model_cfg)

            import torch
            from diffusers.utils import export_to_video, load_image
            from PIL import Image

            # 解析 base64 输入图
            if "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            raw = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(raw)).convert("RGB")

            res = data.get("resolution", "480p")
            height, width = (480, 832) if str(res).lower() == "480p" else (720, 1280)
            num_frames = int(data.get("num_frames", 81))
            num_steps = int(data.get("num_inference_steps", 50))
            guidance = float(data.get("guidance_scale", 5.0))
            guidance_2 = data.get("guidance_scale_2")
            if guidance_2 is not None:
                guidance_2 = float(guidance_2)
            seed = int(data.get("seed", -1))
            fps = int(data.get("frame_rate", 16))

            generator = None
            if seed >= 0:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)

            def _gen():
                import inspect as _inspect
                pipe_kwargs = dict(
                    prompt=prompt,
                    image=image,
                    height=height,
                    width=width,
                    num_frames=num_frames,
                    num_inference_steps=num_steps,
                    guidance_scale=guidance,
                    generator=generator,
                    output_type="np",
                )
                if guidance_2 is not None and getattr(pipe, "transformer_2", None) is not None:
                    sig = _inspect.signature(pipe.__call__)
                    if "guidance_scale_2" in sig.parameters:
                        pipe_kwargs["guidance_scale_2"] = guidance_2
                with torch.no_grad():
                    frames = pipe(**pipe_kwargs)
                vid = frames[0] if isinstance(frames, (tuple, list)) else frames
                return export_to_video(vid, fps=fps)

            loop = asyncio.get_event_loop()
            mp4_bytes = await loop.run_in_executor(None, _gen)
            if isinstance(mp4_bytes, str):
                with open(mp4_bytes, "rb") as f:
                    mp4_bytes = f.read()

            return self._json({
                "data": [{"b64_json": _to_base64_video_bytes(mp4_bytes), "mime": "video/mp4"}],
                "created": int(asyncio.get_event_loop().time()),
            })

        except Exception as e:
            logger.exception("i2v generate error")
            return self._json({"error": str(e)}, 500)


def setup_routes(app: web.Application, manager):
    """注册 Diffusers 桥接路由"""
    h = DiffusersBridgeHandler(manager)
    app.router.add_get("/api/bridge/diffusers/health", h.health)
    app.router.add_post("/api/bridge/diffusers/t2i", h.t2i_generate)
    app.router.add_post("/api/bridge/diffusers/t2v", h.t2v_generate)
    app.router.add_post("/api/bridge/diffusers/i2v", h.i2v_generate)
    # OpenAI 兼容路径
    app.router.add_post("/v1/images/generations", h.t2i_generate)
