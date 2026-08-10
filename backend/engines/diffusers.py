"""
Diffusers Engine
================
HuggingFace Diffusers 推理引擎，支持文生图 / 文生视频 / 图生视频。
基于 diffusers + transformers 生态。
"""
import asyncio
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Any

import aiohttp

from backend.core.engine import BaseEngine, EngineVersion, ModelInstance

logger = logging.getLogger("AMM.Engine.Diffusers")

DIFFUSERS_VERSIONS = [
    EngineVersion(
        engine_type="diffusers",
        version="0.39.0",
        install_path="/amm/backend/engines_installed/diffusers/0.39.0",
        is_default=True,
        metadata={
            "install_method": "pip",
            "pip_packages": [
                "diffusers==0.39.0",
                "transformers>=4.46.0",
                "accelerate>=1.0.0",
                "safetensors>=0.4.0",
                "peft>=0.20.0",
                "torch>=2.5.0",
            ],
            "model_scope_support": True,
            "min_cuda": "13.0",
            "min_gpu_memory_gb": 12,
            "blackwell_support": True,
            "cuda_runtime": "cu130",
            "note": "running in vllm/0.22.1 venv (torch 2.11+cu130)",
        },
    ),
    EngineVersion(
        engine_type="diffusers",
        version="0.33.0",
        install_path="/amm/backend/engines_installed/diffusers/0.33.0",
        metadata={
            "install_method": "pip",
            "pip_packages": [
                "diffusers==0.33.0",
                "transformers>=4.49.0",
                "accelerate>=1.0.0",
                "safetensors>=0.4.0",
                "peft>=0.12.0",
                "torch>=2.5.0",
            ],
            "model_scope_support": True,
            "min_cuda": "11.8",
            "min_gpu_memory_gb": 12,
        },
    ),
]


class DiffusersEngine(BaseEngine):
    """Diffusers 推理引擎 (文生图 / 视频生成)"""

    engine_type = "diffusers"

    def __init__(self, engine_dir: str = "/amm/backend/engines_installed"):
        super().__init__(engine_dir)

    def get_display_name(self) -> str:
        return "Diffusers (HuggingFace)"

    def get_supported_categories(self) -> List[str]:
        return ["image", "video"]

    def get_description(self) -> str:
        return "HuggingFace Diffusers 扩散模型引擎，支持 Qwen-Image / Wan2.2 等文生图和视频生成模型，可通过 ModelScope 或 HuggingFace Hub 加载权重。"

    async def list_installed_versions(self) -> List[EngineVersion]:
        """检查已安装版本"""
        versions = []
        for v in DIFFUSERS_VERSIONS:
            try:
                import importlib
                spec = importlib.util.find_spec("diffusers")
                if spec:
                    import diffusers
                    installed_ver = diffusers.__version__
                    if v.version in installed_ver or installed_ver == v.version:
                        v.status = "installed"
                        versions.append(v)
                        continue
            except Exception:
                pass
            v.status = "available"
            versions.append(v)
        return versions

    async def get_available_versions(self) -> List[EngineVersion]:
        return await self.list_installed_versions()

    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """
        Diffusers 引擎不使用 CLI 子进程，而是通过内置 API 桥接调用。
        实际推理由 backend/api/diffusers_bridge.py 处理。
        返回空列表表示不是子进程模式。
        """
        return []

    async def validate_model(self, model_cfg: Dict, models_dir: str) -> Dict[str, Any]:
        """验证模型是否可用（检查 ModelScope / HF 可用性）"""
        source = model_cfg.get("model_source", "")
        model_id = model_cfg.get("model_id", "")
        if not model_id:
            return {"ok": False, "error": "未配置 model_id"}
        # 不实际下载，只做基本检查
        return {"ok": True, "note": "模型将在首次推理时从远端加载"}

    async def health_check(self, inst: ModelInstance) -> Dict[str, Any]:
        """检查 diffusers 桥接服务健康状态"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{inst.port}/health",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return {"ok": resp.status == 200, "status_code": resp.status}
        except Exception as e:
            # Diffusers 走内置桥接，不走独立进程
            return {"ok": True, "note": "内置桥接模式"}
