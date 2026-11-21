"""
SentenceTransformers Engine
===========================
v1.0 新增：embedding / reranker 专用引擎，基于 sentence-transformers。

与 llama.cpp embedding 的分工:
  - llama.cpp: GGUF 量化模型的 embedding（CUDA/CPU 由 llama-server 决定）
  - sentence_transformers: HF 原生 safetensors 模型（BGE / GTE / M3E 等），
    支持 cpu / cuda 双 flavor，可直接跑在 CPU 上省显存。

两种 flavor 各自独立 venv:
  engines_installed/sentence_transformers/<version>-cpu/venv
  engines_installed/sentence_transformers/<version>-cuda/venv

进程模型: 通过内置 HTTP 子服务 (backend/engines/st_server.py) 对外提供
  /v1/embeddings、/v1/rerank，与其它引擎保持统一的 HTTP 契约。
"""
import logging
import os
from typing import Any, Dict, List

import aiohttp

from backend.core.engine import BaseEngine, EngineVersion, ModelInstance

logger = logging.getLogger("AMM.Engine.SentenceTransformers")

ST_VERSIONS = [
    ("3.3.1", "0.66.0"),
    ("3.0.1", "0.63.0"),
]


def _make_version(version: str, st_version: str, flavor: str, root: str) -> EngineVersion:
    dir_name = f"{version}-{flavor}"
    install_path = os.path.join(root, "sentence_transformers", dir_name)
    return EngineVersion(
        engine_type="sentence_transformers",
        version=version,
        install_path=install_path,
        binary_path=os.path.join(install_path, "venv", "bin", "python"),
        is_default=(version == "3.3.1" and flavor == "cuda"),
        flavor=flavor,
        metadata={
            "install_method": "pip-venv",
            "pip_package": f"sentence-transformers=={version}",
            "torch_flavor": "cpu" if flavor == "cpu" else "cu130",
            "st_version": st_version,
            "categories": ["embedding", "reranker"],
            "note": "CPU flavor 用于 GPU 被大模型占满时跑 embedding",
        },
    )


class SentenceTransformersEngine(BaseEngine):
    """sentence-transformers 推理引擎（embedding / reranker）"""

    engine_type = "sentence_transformers"

    def __init__(self, engine_dir: str = ""):
        super().__init__(engine_dir)
        # BaseEngine 把 engine_dir 拼成 .../sentence_transformers
        self.root = str(self.engine_dir.parent)

    def get_display_name(self) -> str:
        return "sentence-transformers"

    def get_supported_categories(self) -> List[str]:
        return ["embedding", "reranker"]

    def get_description(self) -> str:
        return "HuggingFace sentence-transformers 引擎，支持 BGE/GTE/M3E 等原生向量与重排模型，提供 cpu / cuda 双 flavor（CPU 版不占显存）。"

    def _all_versions(self) -> List[EngineVersion]:
        out = []
        for version, st_version in ST_VERSIONS:
            for flavor in ("cuda", "cpu"):
                out.append(_make_version(version, st_version, flavor, self.root))
        return out

    async def list_installed_versions(self) -> List[EngineVersion]:
        versions = []
        for v in self._all_versions():
            if v.binary_path and os.path.isfile(v.binary_path):
                v.status = "installed"
            else:
                v.status = "available"
            versions.append(v)
        return versions

    async def get_available_versions(self) -> List[EngineVersion]:
        return await self.list_installed_versions()

    def _resolve_python(self, inst: ModelInstance) -> str:
        """定位实例对应 flavor 的 python 解释器"""
        flavor = inst.flavor or "cuda"
        version = inst.engine_version
        candidates = []
        if version:
            candidates.append(os.path.join(self.engine_dir, f"{version}-{flavor}", "venv", "bin", "python"))
            candidates.append(os.path.join(self.engine_dir, version, "venv", "bin", "python"))
        for v, _ in ST_VERSIONS:
            candidates.append(os.path.join(self.engine_dir, f"{v}-{flavor}", "venv", "bin", "python"))
        for c in candidates:
            if os.path.isfile(c):
                return c
        return "python3"

    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """构建 st 子服务启动命令"""
        py = self._resolve_python(inst)
        server_script = os.path.join(os.environ.get("AMM_ROOT", "/amm"), "backend", "engines", "st_server.py")
        model_path = os.path.join(models_dir, inst.selected_model_file) if inst.selected_model_file else ""

        cmd = [
            py, server_script,
            "--host", host,
            "--port", str(model_cfg["port"]),
            "--model", model_path,
            "--category", model_cfg.get("category", "embedding"),
        ]
        params = inst.parameters or {}
        if params.get("device"):
            cmd += ["--device", str(params["device"])]
        elif inst.flavor == "cpu":
            cmd += ["--device", "cpu"]
        if params.get("batch_size"):
            cmd += ["--batch-size", str(params["batch_size"])]
        if params.get("normalize") is False:
            cmd += ["--no-normalize"]
        if params.get("max_seq_length"):
            cmd += ["--max-seq-length", str(params["max_seq_length"])]
        if params.get("trust_remote_code"):
            cmd += ["--trust-remote-code"]
        return cmd

    async def validate_model(self, model_cfg: Dict, models_dir: str) -> Dict[str, Any]:
        available = model_cfg.get("available_models", [])
        if not available:
            return {"ok": False, "error": "未配置模型文件/目录"}
        path = os.path.join(models_dir, available[0].get("file", ""))
        if not os.path.exists(path):
            return {"ok": False, "error": f"模型路径不存在: {path}"}
        return {"ok": True}

    async def health_check(self, inst: ModelInstance) -> Dict[str, Any]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{inst.port}/health",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return {"ok": resp.status == 200, "status_code": resp.status}
        except Exception as e:
            return {"ok": False, "error": str(e)}
