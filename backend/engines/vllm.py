"""
vLLM Engine
===========
高性能 LLM 推理引擎，支持 continuous batching / PagedAttention。
适用于 Chat 和 Embedding 模型。
"""
import asyncio
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Any

import aiohttp

from backend.core.engine import BaseEngine, EngineVersion, ModelInstance

logger = logging.getLogger("AMM.Engine.VLLM")

VLLM_VERSIONS = [
    EngineVersion(
        engine_type="vllm",
        version="0.8.5",
        install_path="/amm/backend/engines_installed/vllm/0.8.5",
        binary_path="/amm/backend/engines_installed/vllm/0.8.5/bin/vllm",
        metadata={
            "install_method": "pip",
            "pip_package": "vllm==0.8.5",
            "min_cuda": "12.0",
            "min_gpu_memory_gb": 16,
        },
    ),
    EngineVersion(
        engine_type="vllm",
        version="0.7.3",
        install_path="/amm/backend/engines_installed/vllm/0.7.3",
        binary_path="/amm/backend/engines_installed/vllm/0.7.3/bin/vllm",
        is_default=False,
        metadata={
            "install_method": "pip",
            "pip_package": "vllm==0.7.3",
            "min_cuda": "12.0",
            "min_gpu_memory_gb": 16,
        },
    ),
]


class VllmEngine(BaseEngine):
    """vLLM 推理引擎"""

    engine_type = "vllm"

    def __init__(self, engine_dir: str = "/amm/backend/engines_installed"):
        super().__init__(engine_dir)

    def get_display_name(self) -> str:
        return "vLLM"

    def get_supported_categories(self) -> List[str]:
        return ["chat", "embedding"]

    def get_description(self) -> str:
        return "高性能 LLM 推理引擎，支持 PagedAttention / continuous batching / tensor parallelism，适用于高并发对话和嵌入场景。"

    async def list_installed_versions(self) -> List[EngineVersion]:
        """检查已安装版本"""
        versions = []
        for v in VLLM_VERSIONS:
            if v.metadata.get("install_method") == "pip":
                try:
                    import importlib
                    spec = importlib.util.find_spec("vllm")
                    if spec:
                        from vllm import __version__ as installed_ver
                        if installed_ver == v.version or v.version in installed_ver:
                            v.status = "installed"
                            versions.append(v)
                            continue
                except Exception:
                    pass
            if v.binary_path and os.path.isfile(v.binary_path):
                v.status = "installed"
                versions.append(v)
            else:
                v.status = "available"
                versions.append(v)
        return versions

    async def get_available_versions(self) -> List[EngineVersion]:
        return await self.list_installed_versions()

    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """构建 vllm serve 命令行

        使用 vLLM venv 内的 python 解释器（修复裸 'python' 导致 
        'No such file or directory' 的问题）。
        """
        model_path = os.path.join(models_dir, inst.selected_model_file)

        # 绑定到 vLLM venv 的 python 解释器（根据 engine_version）
        vllm_python = self._resolve_python(inst)
        cmd = [
            vllm_python, "-m", "vllm.entrypoints.openai.api_server",
            "--model", model_path,
            "--host", host,
            "--port", str(model_cfg["port"]),
        ]

        params = inst.parameters

        if params.get("tensor_parallel_size", 1) > 1:
            cmd += ["--tensor-parallel-size", str(params["tensor_parallel_size"])]
        if params.get("pipeline_parallel_size", 1) > 1:
            cmd += ["--pipeline-parallel-size", str(params["pipeline_parallel_size"])]
        if params.get("max_model_len"):
            cmd += ["--max-model-len", str(params["max_model_len"])]
        if params.get("gpu_memory_utilization"):
            cmd += ["--gpu-memory-utilization", str(params["gpu_memory_utilization"])]
        if params.get("max_num_seqs"):
            cmd += ["--max-num-seqs", str(params["max_num_seqs"])]
        if params.get("dtype"):
            cmd += ["--dtype", params["dtype"]]
        if params.get("quantization"):
            cmd += ["--quantization", params["quantization"]]
        if params.get("enforce_eager", False):
            cmd += ["--enforce-eager"]
        if params.get("trust_remote_code", False):
            cmd += ["--trust-remote-code"]

        return cmd

    def _resolve_python(self, inst: ModelInstance) -> str:
        """根据 engine_version 解析 vLLM venv 的 python 解释器路径"""
        ver = inst.engine_version
        if ver:
            venv_py = f"/amm/backend/engines_installed/vllm/{ver}/venv/bin/python"
            if os.path.isfile(venv_py):
                return venv_py
        # 兜底：在默认安装路径查找
        for probe in ["/amm/backend/engines_installed/vllm/venv/bin/python"]:
            if os.path.isfile(probe):
                return probe
        # 最后 fallback：系统 python3
        return "python3"

    async def validate_model(self, model_cfg: Dict, models_dir: str) -> Dict[str, Any]:
        """验证模型路径"""
        available = model_cfg.get("available_models", [])
        if not available:
            return {"ok": False, "error": "未配置模型路径"}
        model_source = available[0].get("source", "")
        if model_source == "huggingface":
            return {"ok": True}
        model_path = os.path.join(models_dir, available[0].get("file", available[0].get("model_id", "")))
        if not os.path.exists(model_path):
            return {"ok": False, "error": f"模型路径不存在: {model_path}"}
        return {"ok": True}

    async def health_check(self, inst: ModelInstance) -> Dict[str, Any]:
        """检查 vLLM 健康状态"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{inst.port}/health",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return {"ok": resp.status == 200, "status_code": resp.status}
        except Exception as e:
            return {"ok": False, "error": str(e)}
