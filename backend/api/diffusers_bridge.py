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
    """异步加载 diffusers pipeline (独立线程执行，阻塞式)"""
    model_id = model_cfg.get("model_id", "")
    category = model_cfg.get("category", "")
    model_source = model_cfg.get("model_source", "modelscope")

    cache_key = f"{category}:{model_id}"
    if cache_key in _pipeline_cache:
        return _pipeline_cache[cache_key]

    logger.info(f"Loading {category} pipeline: {model_id} (source={model_source})")

    def _load():
        import torch
        import json as _json

        local = _resolve_local_path(model_id)
        logger.info(f"Local model path: {local}")

        if category == "image":
            from diffusers import QwenImagePipeline
            pipe = QwenImagePipeline.from_pretrained(
                local,
                torch_dtype=torch.bfloat16,
                variant="bf16",
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

            pipe = pipe_cls.from_pretrained(
                local,
                torch_dtype=torch.bfloat16,
                variant="bf16",
            )

        # 显存优化: 模型 CPU offload，按子模块序列化搬运，避免 84G 一次吃满
        try:
            pipe.enable_model_cpu_offload()
            logger.info("enable_model_cpu_offload enabled")
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
            seed = int(data.get("seed", -1))
            fps = int(data.get("frame_rate", 16))

            generator = None
            if seed >= 0:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)

            def _gen():
                with torch.no_grad():
                    frames = pipe(
                        prompt=prompt,
                        height=height,
                        width=width,
                        num_frames=num_frames,
                        num_inference_steps=num_steps,
                        guidance_scale=guidance,
                        generator=generator,
                        output_type="np",
                    )
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
            seed = int(data.get("seed", -1))
            fps = int(data.get("frame_rate", 16))

            generator = None
            if seed >= 0:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)

            def _gen():
                with torch.no_grad():
                    frames = pipe(
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
