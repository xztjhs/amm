"""
Diffusers Bridge API
====================
内置桥接，为文生图/视频提供推理接口，不走子进程。
启动时按需加载 pipeline，首次推理延迟较高。
"""
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

from aiohttp import web

logger = logging.getLogger("AMM.DiffusersBridge")

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")

# 惰性加载的 pipeline 缓存
_pipeline_cache: Dict[str, Any] = {}


def _get_cache_dir(model_id: str) -> str:
    """ModelScope / HuggingFace 缓存目录"""
    scope = os.environ.get("MODELSCOPE_CACHE", os.path.join(MODELS_DIR, "modelscope"))
    hf = os.environ.get("HF_HOME", os.path.join(MODELS_DIR, "huggingface"))
    return scope


async def _load_pipeline(model_cfg: Dict) -> Any:
    """异步加载 diffusers pipeline (在独立线程中执行)"""
    model_id = model_cfg.get("model_id", "")
    model_source = model_cfg.get("model_source", "modelscope")
    category = model_cfg.get("category", "")

    cache_key = f"{category}:{model_id}"
    if cache_key in _pipeline_cache:
        return _pipeline_cache[cache_key]

    logger.info(f"Loading diffusers pipeline: {model_id} (source={model_source})")

    def _load():
        try:
            import torch
            from diffusers import DiffusionPipeline

            if model_source == "modelscope":
                from modelscope import snapshot_download
                local_path = snapshot_download(model_id, cache_dir=_get_cache_dir(model_id))
                pipe = DiffusionPipeline.from_pretrained(
                    local_path,
                    torch_dtype=torch.bfloat16,
                )
            else:
                pipe = DiffusionPipeline.from_pretrained(
                    model_id,
                    torch_dtype=torch.bfloat16,
                    cache_dir=_get_cache_dir(model_id),
                )

            # 自动选择设备
            if torch.cuda.is_available():
                pipe = pipe.to("cuda")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                pipe = pipe.to("mps")

            return pipe
        except Exception as e:
            logger.error(f"Pipeline load failed: {e}")
            raise

    loop = asyncio.get_event_loop()
    pipe = await loop.run_in_executor(None, _load)
    _pipeline_cache[cache_key] = pipe
    logger.info(f"Pipeline loaded: {cache_key}")
    return pipe


class DiffusersBridgeHandler:
    """Diffusers 桥接 HTTP 处理器"""

    def __init__(self, manager):
        self.manager = manager

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                  headers={"Access-Control-Allow-Origin": "*"})

    async def health(self, req):
        return self._json({"status": "ok", "bridge": "diffusers"})

    async def t2i_generate(self, req):
        """文生图 /v1/images/generations"""
        try:
            data = await req.json()
            prompt = data.get("prompt", "")
            if not prompt:
                return self._json({"error": "prompt required"}, 400)

            model_cfg = self._find_model_cfg("t2i")
            pipe = await _load_pipeline(model_cfg)

            width = data.get("width", 1024)
            height = data.get("height", 1024)
            num_steps = data.get("num_inference_steps", 28)
            guidance = data.get("guidance_scale", 5.0)
            seed = data.get("seed", -1)
            num_images = min(data.get("n", 1), 4)

            import torch
            generator = None
            if seed >= 0:
                generator = torch.Generator(device=pipe.device).manual_seed(seed)

            def _gen():
                with torch.no_grad():
                    result = pipe(
                        prompt=prompt,
                        width=width,
                        height=height,
                        num_inference_steps=num_steps,
                        guidance_scale=guidance,
                        num_images_per_prompt=num_images,
                        generator=generator,
                    )
                return result.images

            loop = asyncio.get_event_loop()
            images = await loop.run_in_executor(None, _gen)

            # 编码为 base64
            import PIL.Image
            b64_list = []
            for img in images:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                b64_list.append({"b64_json": b64})

            return self._json({"data": b64_list, "created": int(asyncio.get_event_loop().time())})

        except Exception as e:
            logger.exception("t2i generate error")
            return self._json({"error": str(e)}, 500)

    async def t2v_generate(self, req):
        """文生视频 (placeholder - 实际实现取决于 wan2.1 pipeline 接口)"""
        return self._json({"error": "t2v not yet fully implemented", "note": "Wan2.2 pipeline loading requires specific diffusers version"}, 501)

    async def i2v_generate(self, req):
        """图生视频 (placeholder)"""
        return self._json({"error": "i2v not yet fully implemented"}, 501)

    def _find_model_cfg(self, model_id: str) -> Optional[Dict]:
        return self.manager._find_model_config(model_id)


def setup_routes(app: web.Application, manager):
    """注册 Diffusers 桥接路由"""
    h = DiffusersBridgeHandler(manager)
    app.router.add_get("/api/bridge/diffusers/health", h.health)
    app.router.add_post("/api/bridge/diffusers/t2i", h.t2i_generate)
    app.router.add_post("/api/bridge/diffusers/t2v", h.t2v_generate)
    app.router.add_post("/api/bridge/diffusers/i2v", h.i2v_generate)
    # OpenAI 兼容路径
    app.router.add_post("/v1/images/generations", h.t2i_generate)
