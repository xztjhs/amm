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
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from aiohttp import web

logger = logging.getLogger("AMM.DiffusersBridge")

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
MODELSCOPE_CACHE = os.environ.get("MODELSCOPE_CACHE", os.path.join(MODELS_DIR, "zoo", "modelscope"))

# 每次推理完成后自动调用 CUDA 释放 (空缓存) (v0.7.3)
#   默认开启; 环境变量 AMM_AUTO_RELEASE_GPU=0 关闭
AUTO_RELEASE_GPU = os.environ.get("AMM_AUTO_RELEASE_GPU", "1").strip().lower() not in ("0", "false", "off", "no")

# 验证产物输出目录 (T2I PNG / T2V MP4)
# 可通过环境变量 VERIFICATION_DIR 覆盖, 默认 /amm/verification
# 在启动时自动创建 (权限允许则失败警告, 不报错)
VERIFICATION_DIR = os.environ.get("VERIFICATION_DIR", "/amm/verification")
try:
    os.makedirs(VERIFICATION_DIR, exist_ok=True)
except Exception as _e:
    logger.warning(f"创建 VERIFICATION_DIR={VERIFICATION_DIR} 失败: {_e}, 回退到 /tmp")
    VERIFICATION_DIR = "/tmp"

# per-model 日志目录 (与 ModelManager.LOGS_DIR 对齐, 也兼容独立运行)
_DEFAULT_LOGS = os.path.join(os.environ.get("AMM_ROOT", "/amm"), "logs")
_LOGS_DIR = os.environ.get("LOGS_DIR", _DEFAULT_LOGS)
try:
    os.makedirs(_LOGS_DIR, exist_ok=True)
except Exception:
    _LOGS_DIR = "/tmp"

# 惰性加载的 pipeline 缓存
_pipeline_cache: Dict[str, Any] = {}

# ============================================================
# 活动任务注册中心 (供 Dashboard / 计时 / 实时日志)
#   记录: 提交 -> 进入推理 -> 完成 的全过程时间戳
# ============================================================
_TASK_LOCK = asyncio.Lock()
_ACTIVE_TASKS: Dict[str, Dict[str, Any]] = {}   # task_id -> task record
_TASK_SEQ = 0


def _write_model_log(model_id: str, line: str) -> None:
    """把一行日志追加到 <logs>/{model_id}_server.log (与 get_model_logs 对齐)"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(_LOGS_DIR, f"{model_id}_server.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except Exception as e:
        logger.warning(f"写 model log({model_id}) 失败: {e}")


async def _task_begin(model_id: str, **meta) -> str:
    """新建一条推理任务记录 (提交时刻), 返回 task_id"""
    global _TASK_SEQ
    async with _TASK_LOCK:
        _TASK_SEQ += 1
        task_id = f"{model_id}-{int(time.time()*1000)}"
        _ACTIVE_TASKS[task_id] = {
            "task_id": task_id,
            "model_id": model_id,
            "seq": _TASK_SEQ,
            "submitted_at": time.time(),
            "started_at": None,       # 进入实际推理
            "finished_at": None,
            "status": "submitted",
            "elapsed_submit_to_run": None,   # 提交 -> 开始推理
            "elapsed_total": None,           # 提交 -> 完成
            "error": None,
            "saved_paths": [],
            **meta,
        }
    _model_log(model_id, f"[task {task_id}] 提交任务", extra=meta)
    return task_id


async def _task_phase(model_id: str, task_id: str, phase: str, **meta) -> None:
    async with _TASK_LOCK:
        t = _ACTIVE_TASKS.get(task_id)
        if t is None:
            return
        now = time.time()
        if phase in ("running", "infer"):
            t["started_at"] = t["started_at"] or now
            t["status"] = "running"
            t["elapsed_submit_run"] = now - t["submitted_at"]
        elif phase == "complete":
            t["finished_at"] = now
            t["status"] = "completed"
            t["elapsed_total"] = now - t["submitted_at"]
        elif phase == "error":
            t["finished_at"] = now
            t["status"] = "error"
            t["error"] = meta.get("error")
            t["elapsed_total"] = now - t["submitted_at"]
        for k, v in meta.items():
            if k != "error":
                t[k] = v


def _model_log(model_id: str, line: str, extra: Optional[dict] = None) -> None:
    """写一条带可选 K=V 后缀的日志"""
    if extra:
        kv = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
        if kv:
            line = f"{line} ({kv})"
    _write_model_log(model_id, line)


def _maybe_release_gpu(model_id: str) -> None:
    """推理完成后按开关自动释放 GPU (v0.7.3)

    AMM_AUTO_RELEASE (默认 full):
      full  -> 彻底卸载 pipeline (清 cache + 释放权重), GPU 切实回低占用
      cache -> 仅 empty_cache 临时碎片, 保留模型 (适合连续推理性能)
    """
    if not AUTO_RELEASE_GPU:
        return
    mode = os.environ.get("AMM_AUTO_RELEASE", "full").strip().lower()
    _release_gpu(model_id=model_id, keep_pipeline=(mode != "full"))


def _release_gpu(model_id: Optional[str] = None, keep_pipeline: bool = True) -> None:
    """释放 GPU 显存 (v0.7.3)

    - keep_pipeline=True : 仅 gc + 空缓存, 保留已加载模型 (释放临时/碎片)
    - keep_pipeline=False: 彻底卸载 pipeline 缓存 + gc + 空缓存, 释放权重,
      推理完成后 GPU 占用回落到基线。
    """
    if not keep_pipeline:
        _pipeline_cache.clear()
    try:
        import gc, torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            rlog = f"CUDA 显存已自动释放 (keep_pipeline={'是' if keep_pipeline else '否'})"
        else:
            rlog = "CUDA 不可用, 跳过释放"
    except Exception as e:
        rlog = f"GPU 释放跳过: {e}"
    if model_id:
        _model_log(model_id, f"[auto-release] {rlog}")
    logger.info(f"diffusers auto-release ({model_id}): {rlog}")

def list_active_tasks() -> List[Dict[str, Any]]:
    """汇总进行中/最近完成的任务 (不含锁, 供事件循环外读数)"""
    now = time.time()
    out = []
    for t in _ACTIVE_TASKS.values():
        if t["status"] == "running" or (t["finished_at"] and now - t["finished_at"] < 600):
            d = dict(t)
            d["elapsed_total"] = (
                (t["elapsed_total"] or (now - t["submitted_at"] if t["status"] == "running" else None))
            )
            d["elapsed_run"] = (now - t["started_at"]) if t["started_at"] and t["status"] == "running" else None
            out.append(d)
    return sorted(out, key=lambda x: x["seq"], reverse=True)


def _trim(s: Any, n: int = 80) -> str:
    """截断 prompt 用于日志/展示"""
    if s is None:
        return ""
    s = str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"

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
      - 支持 FP8 layerwise_casting 量化加载
      - 支持 group_offload 后备方案
      - 保留 enable_model_cpu_offload

    offload 策略 (2026-08-08 显式化, 解决 t2v/i2v GPU 不工作):
      model_cfg["offload"]:  "gpu"   -> 全模型驻留 GPU (Wan2.2-A14B FP8 ~28G, 84G 可期; GPU 利用率最高, 推荐)
                             "model" -> enable_model_cpu_offload (序列层级 CPU offload, 旧默认)
                             "group" -> leaf_level group offload (显存极紧时)
      兼容旧字段 cpu_offload (bool):
        不设/None        -> 默认 "gpu"
        cpu_offload=true -> "group"
        cpu_offload=false-> "gpu"
    """
    model_id = model_cfg.get("model_id", "")
    category = model_cfg.get("category", "")
    model_source = model_cfg.get("model_source", "modelscope")

    cache_key = f"{category}:{model_id}:quant={model_cfg.get('quant','default')}:offload={_resolve_offload(model_cfg)}"
    if cache_key in _pipeline_cache:
        return _pipeline_cache[cache_key]

    _model_log(model_id, f"开始加载 pipeline (source={model_source}, quant={model_cfg.get('quant','default')})")
    logger.info(f"Loading {category} pipeline: {model_id} (source={model_source}, quant={model_cfg.get('quant','default')})")

    def _load():
        import torch
        import json as _json

        local = _resolve_local_path(model_id)
        logger.info(f"Local model path: {local}")

        # ---- 计算 / 存储 dtype ----
        if category == "image":
            compute_dtype = _torch_dtype(model_cfg.get("compute_dtype", "bf16")) or torch.bfloat16
            storage_dtype = compute_dtype
        else:
            compute_dtype = _torch_dtype(model_cfg.get("compute_dtype", "bf16")) or torch.bfloat16
            storage_dtype = compute_dtype
            vae_dtype = torch.float32

        if category == "image":
            from diffusers import QwenImagePipeline
            pipe = QwenImagePipeline.from_pretrained(
                local,
                torch_dtype=compute_dtype,
            )
        else:
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

            boundary_ratio = model_cfg.get("boundary_ratio")
            if boundary_ratio is None and "Wan2.2" in model_id:
                boundary_ratio = 0.875
            logger.info(f"Wan MoE boundary_ratio = {boundary_ratio}")

            load_kwargs = dict(
                torch_dtype=compute_dtype,
            )
            if boundary_ratio is not None:
                load_kwargs["boundary_ratio"] = float(boundary_ratio)

            pipe = pipe_cls.from_pretrained(local, **load_kwargs)

        # ---- FP8 layerwise_casting ----
        if _should_enable_layerwise_casting(model_cfg):
            _apply_layerwise_casting(pipe)

        # ---- 显存搬运策略 (2026-08-08 显式) ----
        offload = _resolve_offload(model_cfg)
        _model_log(model_id, f"offload 策略 = {offload}")
        if offload == "group":
            _apply_group_offload(pipe, offload_to_cpu=True)
            try:
                if torch.cuda.is_available() and getattr(pipe, "vae", None) is not None:
                    pipe.vae.to("cuda")
            except Exception as e:
                logger.warning(f"VAE 移回 GPU 失败: {e}")
        elif offload == "model":
            try:
                pipe.enable_model_cpu_offload()
                logger.info("enable_model_cpu_offload enabled (model offload)")
            except Exception as e:
                logger.warning(f"enable_model_cpu_offload 失败: {e}")
                if torch.cuda.is_available():
                    pipe = pipe.to("cuda")
        else:  # gpu - 全驻 GPU
            if torch.cuda.is_available():
                pipe = pipe.to("cuda")
                logger.info(f"全模型驻留 GPU (to cuda), offload={offload}")
            else:
                logger.warning("CUDA 不可用, diffusers 回退 CPU 推理")

        return pipe

    loop = asyncio.get_event_loop()
    try:
        pipe = await loop.run_in_executor(None, _load)
    except Exception as e:
        logger.exception(f"Pipeline load failed for {model_id}")
        _model_log(model_id, f"Pipeline 加载失败: {e}")
        raise
    _pipeline_cache[cache_key] = pipe
    _model_log(model_id, "Pipeline 加载完成")
    logger.info(f"Pipeline loaded: {cache_key}")
    return pipe


def _resolve_offload(model_cfg: Dict) -> str:
    """解析 offload 策略, 返回 'gpu' | 'model' | 'group' (优先新字段 offload, 兼容旧 cpu_offload)"""
    off = model_cfg.get("offload") if model_cfg.get("offload") not in (None, "") else None
    if off:
        off = str(off).lower().strip()
        if off in ("gpu", "full", "cuda", "none"):
            return "gpu"
        if off in ("model", "sequence", "seq"):
            return "model"
        if off in ("group", "leaf", "leaf_level"):
            return "group"
    co = model_cfg.get("cpu_offload")
    if co is True:
        return "group"
    if co is False or co is None:
        return "gpu"
    s = str(co).lower().strip()
    if s in ("true", "1", "on"):
        return "group"
    if s in ("0", "false", "off", "none"):
        return "gpu"
    if s in ("model", "seq"):
        return "model"
    return "group"


def _to_base64_png(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _to_base64_video_bytes(raw_bytes: bytes) -> str:
    return base64.b64encode(raw_bytes).decode()


def _extract_video_frames(pipe_out) -> "np.ndarray":
    """从 Wan 等 pipeline 输出中提取可传给 export_to_video 的帧序列

    diffusers 0.39 的 WanPipeline 在 output_type='np' 下返回
    WanPipelineOutput (有序 dict), 其 frames 为 5D ndarray (1, num_frames, H, W, C)。
    export_to_video 需要 4D (num_frames, H, W, C)。
    """
    import numpy as np
    # 1) 优先取 .frames (WanPipelineOutput 等规范输出)
    frames = getattr(pipe_out, "frames", None)
    if frames is None and isinstance(pipe_out, (tuple, list)):
        # 兼容老版本 tuple/list 返回: (video, meta) 或 (frames,)
        if len(pipe_out) >= 1:
            frames = pipe_out[0]
    if frames is None and hasattr(pipe_out, "videos"):
        frames = pipe_out.videos
    if frames is None:
        raise TypeError(f"无法从 pipeline 输出提取帧: {type(pipe_out)}")

    arr = np.asarray(frames)
    if arr.ndim == 5:  # (1, num_frames, H, W, C) -> squeeze batch
        arr = arr[0]
    return arr


def _save_png(img, model_id: str, seed: int = -1) -> Optional[str]:
    """保存 PNG 到 VERIFICATION_DIR, 返回绝对路径 (失败返回 None)

    文件名: <model_short>__seed<seed>__<ts>.png
    例: qwen_image__seed42__20260805-201530.png
    """
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        short = model_id.split("/")[-1].lower().replace(".", "_").replace("-", "_") if model_id else "unknown"
        seed_part = f"seed{seed}" if seed >= 0 else "noseed"
        fname = f"{short}__{seed_part}__{ts}.png"
        fpath = os.path.join(VERIFICATION_DIR, fname)
        img.save(fpath, format="PNG")
        return fpath
    except Exception as e:
        logger.warning(f"save_png 失败: {e}")
        return None


def _save_video_bytes(raw_bytes: bytes, model_id: str, seed: int = -1) -> Optional[str]:
    """保存 MP4 到 VERIFICATION_DIR, 返回绝对路径"""
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        short = model_id.split("/")[-1].lower().replace(".", "_").replace("-", "_") if model_id else "unknown"
        seed_part = f"seed{seed}" if seed >= 0 else "noseed"
        fname = f"{short}__{seed_part}__{ts}.mp4"
        fpath = os.path.join(VERIFICATION_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(raw_bytes)
        return fpath
    except Exception as e:
        logger.warning(f"save_video 失败: {e}")
        return None



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
        return self._json({
            "status": "ok",
            "bridge": "diffusers",
            "verification_dir": VERIFICATION_DIR,
            "verification_dir_writable": os.access(VERIFICATION_DIR, os.W_OK),
        })

    async def status(self, req):
        """活动任务状态 (供 Dashboard / 前端轮询, 实时耗时)"""
        # 附带已加载的 pipeline 缓存情况 (显存占用诊断)
        loaded = [{"key": k, "loaded": True} for k in _pipeline_cache.keys()]
        total_mb = 0
        try:
            import torch
            if torch.cuda.is_available():
                total_mb = round(torch.cuda.memory_allocated() / 1024 ** 2, 1)
        except Exception:
            pass
        return self._json({"tasks": list_active_tasks(), "pipeline_cache": loaded, "gpu_allocated_mb": total_mb})

    async def unload(self, req):
        """卸载指定/所有 diffusers pipeline, 释放 GPU 显存 (推理服务 stop)

        请求体: { model: "t2i" | "t2v" | "i2v" | "all" (默认 all) }
        对进行中的推理任务无影响(已完成/失败的任务已不在 pipeline 缓存依赖内)。
        """
        try:
            data = await req.json()
        except Exception:
            data = {}
        model = str(data.get("model", "all") or "all").lower().strip()

        before = len(_pipeline_cache)
        removed = []
        if model == "all":
            removed = list(_pipeline_cache.keys())
            _pipeline_cache.clear()
        else:
            # 按 model 前缀/名匹配 (cache_key 形如 video:xxx:quant=..)
            for k in list(_pipeline_cache.keys()):
                if model in k or k.startswith(model + ":"):
                    removed.append(k)
                    del _pipeline_cache[k]

        # 释放 GPU 显存
        freed_mb = 0
        try:
            import gc, torch
            gc.collect()
            if torch.cuda.is_available():
                freed_mb = round(torch.cuda.memory_allocated() / 1024 ** 2, 1)
                torch.cuda.empty_cache()
                freed_mb = round(freed_mb - torch.cuda.memory_allocated() / 1024 ** 2, 1)
        except Exception as e:
            logger.warning(f"unload 释放显存异常: {e}")

        _model_log(model, f"[unload] 卸载 {len(removed)} 个 pipeline, 释放显存约 {freed_mb} MB")
        logger.info(f"diffusers unload model={model}: {len(removed)} 个 pipeline 卸载 (cache {before}->{len(_pipeline_cache)}), gpu freed ~{freed_mb} MB")
        return self._json({
            "ok": True,
            "model": model,
            "removed": removed,
            "before": before,
            "after": len(_pipeline_cache),
            "gpu_freed_mb": freed_mb,
        })

    async def preload(self, req):
        """预加载指定 diffusers 模型到 GPU (v0.7.1: 手动 warm-up, 不随容器自启)

        请求体: { model: "t2i" | "t2v" | "i2v" }
        用于提前加载模型, 避免首次请求的冷启动延迟。
        """
        try:
            data = await req.json()
        except Exception:
            data = {}
        model = str(data.get("model", "") or "").lower().strip()
        if model not in ("t2i", "t2v", "i2v"):
            return self._json({"error": "model 必须是 t2i/t2v/i2v"}, 400)

        try:
            t0 = time.time()
            model_cfg = self.manager.config.get(f"{model}_model")
            if not model_cfg:
                return self._json({"error": f"{model} 模型未配置"}, 404)
            pipe = await _load_pipeline(model_cfg)
            el = round(time.time() - t0, 1)
            _model_log(model, f"[preload] 预加载完成, 耗时 {el}s")
            return self._json({"ok": True, "model": model, "loaded_in_s": el})
        except Exception as e:
            logger.exception(f"preload {model} failed")
            _model_log(model, f"[model] 预加载失败: {e}")
            return self._json({"error": str(e)}, 500)

    async def download(self, req):
        """下载保存的产物文件 (PNG / MP4), 限定在 VERIFICATION_DIR 内防目录穿越"""
        path = req.query.get("path", "")
        if not path:
            return self._json({"error": "path required"}, 400)
        try:
            p = Path(path).resolve()
            vdir = Path(VERIFICATION_DIR).resolve()
            if not (str(p).startswith(str(vdir)) or p in (vdir,)):
                return self._json({"error": "path 越界"}, 403)
            if not p.is_file():
                return self._json({"error": "文件不存在: " + str(p)}, 404)
            return web.FileResponse(str(p), headers={"Access-Control-Allow-Origin": "*"})
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # ---- /v1/videos/generations (OpenAI 风格统一入口) ----
    async def videos_generate(self, req):
        """OpenAI 风格的视频生成入口: 根据请求体 video_type 路由到 t2v / i2v

        请求体示例:
          { "prompt": "...", "video_type": "t2v", "save_to_disk": true, "seed": 42 }
          { "prompt": "...", "video_type": "i2v", "image": "<base64>", "save_to_disk": true }
          不传 video_type 则默认 t2v
        """
        try:
            data = await req.json()
            video_type = (data.get("video_type") or "t2v").lower()
            if video_type not in ("t2v", "i2v"):
                return self._json({"error": f"video_type 必须是 t2v 或 i2v, 收到 {video_type!r}"}, 400)
            # 转发到对应子 handler
            if video_type == "t2v":
                return await self.t2v_generate(req)
            return await self.i2v_generate(req)
        except Exception as e:
            logger.exception("videos_generate error")
            return self._json({"error": str(e)}, 500)

    # ---- T2I: 文生图 ----
    async def t2i_generate(self, req):
        model_id = "t2i"
        task_id = await _task_begin(model_id)
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            await _task_phase(model_id, task_id, "submitted", prompt=_trim(prompt), width=data.get("width"), height=data.get("height"))
            if not prompt:
                _model_log(model_id, f"[task {task_id}] 缺少 prompt")
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

            await _task_phase(model_id, task_id, "running", started=True)
            loop = asyncio.get_event_loop()
            images = await loop.run_in_executor(None, _gen)

            data_list = [{"b64_json": _to_base64_png(img)} for img in images]
            resp = {"data": data_list, "created": int(asyncio.get_event_loop().time())}

            # 默认落盘 (不管 save_to_disk), 便于拿文件; 同时返回可下载 URL
            saved_paths = []
            for img in images:
                p = _save_png(img, model_cfg.get("model_id", ""), seed=seed)
                if p:
                    saved_paths.append(p)
            if saved_paths:
                resp["saved_paths"] = saved_paths
                resp["download_urls"] = ["/api/bridge/diffusers/download?path=" + urllib.parse.quote(p) for p in saved_paths]

            await _task_phase(model_id, task_id, "complete", saved_paths=saved_paths)
            _model_log(model_id, f"[task {task_id}] 完成: {len(saved_paths)} 张图", extra={"saved": saved_paths})
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json(resp)

        except Exception as e:
            logger.exception("t2i generate error")
            await _task_phase(model_id, task_id, "error", error=str(e))
            _model_log(model_id, f"[task {task_id}] 异常: {e}")
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json({"error": str(e)}, 500)

    # ---- T2V: 文生视频 ----
    async def t2v_generate(self, req):
        model_id = "t2v"
        task_id = await _task_begin(model_id)
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            await _task_phase(model_id, task_id, "submitted", prompt=_trim(prompt), res=data.get("resolution"), frames=data.get("num_frames"))
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
                if guidance_2 is not None and getattr(pipe, "transformer_2", None) is not None:
                    sig = _inspect.signature(pipe.__call__)
                    if "guidance_scale_2" in sig.parameters:
                        pipe_kwargs["guidance_scale_2"] = guidance_2
                with torch.no_grad():
                    frames = pipe(**pipe_kwargs)
                vid = _extract_video_frames(frames)
                return export_to_video(vid, fps=fps)

            await _task_phase(model_id, task_id, "running", model=True)
            loop = asyncio.get_event_loop()
            mp4_bytes = await loop.run_in_executor(None, _gen)
            if isinstance(mp4_bytes, str):  # 返回文件路径
                with open(mp4_bytes, "rb") as f:
                    mp4_bytes = f.read()

            resp = {
                "data": [{"b64_json": _to_base64_video_bytes(mp4_bytes), "mime": "video/mp4"}],
                "created": int(asyncio.get_event_loop().time()),
            }
            # 默认落盘 + 下载 URL
            p = _save_video_bytes(mp4_bytes, model_cfg.get("model_id", ""), seed=seed)
            if p:
                resp["saved_paths"] = [p]
                resp["download_urls"] = ["/api/bridge/diffusers/download?path=" + urllib.parse.quote(p)]
                _model_log(model_id, f"[task {task_id}] 完成, {len(mp4_bytes)} bytes", {"saved": p, "res": res})
            else:
                _model_log(model_id, f"[task {task_id}] 完成但落盘失败")
            await _task_phase(model_id, task_id, "complete", saved_paths=resp.get("saved_paths"), bytes=len(mp4_bytes))
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json(resp)

        except Exception as e:
            logger.exception("t2v generate error")
            await _task_phase(model_id, task_id, "error", error=str(e))
            _model_log(model_id, f"[task {task_id}] 异常: {e}")
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json({"error": str(e)}, 500)

    # ---- I2V: 图生视频 ----
    async def i2v_generate(self, req):
        model_id = "i2v"
        task_id = await _task_begin(model_id)
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            await _task_phase(model_id, task_id, "submitted", prompt=_trim(prompt), res=data.get("resolution"), frames=data.get("num_frames"))
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
                vid = _extract_video_frames(frames)
                return export_to_video(vid, fps=fps)

            await _task_phase(model_id, task_id, "running", model=True)
            loop = asyncio.get_event_loop()
            mp4_bytes = await loop.run_in_executor(None, _gen)
            if isinstance(mp4_bytes, str):
                with open(mp4_bytes, "rb") as f:
                    mp4_bytes = f.read()

            resp = {
                "data": [{"b64_json": _to_base64_video_bytes(mp4_bytes), "mime": "video/mp4"}],
                "created": int(asyncio.get_event_loop().time()),
            }
            p = _save_video_bytes(mp4_bytes, model_cfg.get("model_id", ""), seed=seed)
            if p:
                resp["saved_paths"] = [p]
                resp["download_urls"] = ["/api/bridge/diffusers/download?path=" + urllib.parse.quote(p)]
                logger.info(f"i2v saved MP4 -> {p}")
            await _task_phase(model_id, task_id, "complete", saved_paths=resp.get("saved_paths"), bytes=len(mp4_bytes))
            _model_log(model_id, f"[task {task_id}] 完成, {len(mp4_bytes)} bytes", {"saved": p})
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json(resp)

        except Exception as e:
            logger.exception("i2v generate error")
            await _task_phase(model_id, task_id, "error", error=str(e))
            _model_log(model_id, f"[task {task_id}] 异常: {e}")
            try: del pipe
            except NameError: pass
            _maybe_release_gpu(model_id)
            return self._json({"error": str(e)}, 500)


def setup_routes(app: web.Application, manager):
    """注册 Diffusers 桥接路由"""
    h = DiffusersBridgeHandler(manager)
    app.router.add_get("/api/bridge/diffusers/health", h.health)
    app.router.add_get("/api/bridge/diffusers/status", h.status)
    app.router.add_get("/api/bridge/diffusers/download", h.download)
    app.router.add_post("/api/bridge/diffusers/unload", h.unload)
    app.router.add_post("/api/bridge/diffusers/preload", h.preload)
    app.router.add_post("/api/bridge/diffusers/t2i", h.t2i_generate)
    app.router.add_post("/api/bridge/diffusers/t2v", h.t2v_generate)
    app.router.add_post("/api/bridge/diffusers/i2v", h.i2v_generate)
    # OpenAI 兼容路径
    app.router.add_post("/v1/images/generations", h.t2i_generate)
    # 2026-08-05: 视频生成也加 OpenAI 兼容路径, 路由到 T2V/I2V
    # video: "t2v" | "i2v" 由请求体里的 video_type 决定, 不提供则默认 t2v
    app.router.add_post("/v1/videos/generations", h.videos_generate)
