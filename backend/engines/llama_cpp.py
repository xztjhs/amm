"""
llama.cpp Engine
================
GGUF 模型推理引擎，支持 Chat / Embedding / ASR / TTS / Reranker / OCR。
"""
import asyncio
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Any

import aiohttp

from backend.core.engine import BaseEngine, EngineVersion, ModelInstance

logger = logging.getLogger("AMM.Engine.LlamaCpp")

LLAMACPP_VERSIONS = [
    EngineVersion(
        engine_type="llama_cpp",
        version="b4727",
        install_path="/usr/local/bin/llama-server",
        binary_path="/usr/local/bin/llama-server",
        is_default=True,
        metadata={
            "repo": "https://github.com/ggerganov/llama.cpp",
            "build_cmd": "cmake -B build -DGGML_CUDA=ON && cmake --build build -j$(nproc) --target llama-server",
        },
    ),
    EngineVersion(
        engine_type="llama_cpp",
        version="latest",
        install_path="/amm/backend/engines_installed/llama_cpp/latest",
        binary_path="/amm/backend/engines_installed/llama_cpp/latest/bin/llama-server",
        metadata={
            "repo": "https://github.com/ggerganov/llama.cpp",
            "build_cmd": "cmake -B build -DGGML_CUDA=ON && cmake --build build -j$(nproc) --target llama-server",
        },
    ),
]


class LlamaCppEngine(BaseEngine):
    """llama.cpp 推理引擎"""

    engine_type = "llama_cpp"

    def __init__(self, engine_dir: str = "/amm/backend/engines_installed"):
        super().__init__(engine_dir)

    def get_display_name(self) -> str:
        return "llama.cpp (GGUF)"

    def get_supported_categories(self) -> List[str]:
        return ["chat", "embedding", "asr", "tts", "reranker", "ocr"]

    def get_description(self) -> str:
        return "高性能 GGUF 量化模型推理引擎，支持 CPU/GPU 混合推理，适用于 LLM / Embedding / ASR / TTS / Reranker / OCR 等文本和多模态模型。"

    async def list_installed_versions(self) -> List[EngineVersion]:
        """检查已安装版本"""
        versions = []
        for v in LLAMACPP_VERSIONS:
            if v.binary_path and os.path.isfile(v.binary_path):
                v.status = "installed"
                versions.append(v)
            elif v.binary_path and os.path.isdir(os.path.dirname(v.binary_path)):
                v.status = "available"
                versions.append(v)
        return versions

    async def get_available_versions(self) -> List[EngineVersion]:
        """返回可安装版本列表"""
        installed = {v.version: v for v in await self.list_installed_versions()}
        result = []
        for v in LLAMACPP_VERSIONS:
            if v.version in installed:
                result.append(installed[v.version])
            else:
                v.status = "available"
                result.append(v)
        return result

    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """构建 llama-server 命令行"""
        server_bin = "/usr/local/bin/llama-server"
        # 如果指定了版本，尝试使用对应版本的 binary
        if inst.engine_version and inst.engine_version not in ("b4727",):
            for v in await self.list_installed_versions():
                if v.version == inst.engine_version and v.binary_path:
                    server_bin = v.binary_path
                    break

        model_path = os.path.join(models_dir, inst.selected_model_file)
        cmd = [
            server_bin,
            "-m", model_path,
            "--host", host,
            "--port", str(model_cfg["port"]),
        ]

        params = inst.parameters
        category = model_cfg.get("category", "")

        # GPU 层数
        n_gpu_layers = params.get("n_gpu_layers", -1)
        if n_gpu_layers >= 0:
            cmd += ["--n-gpu-layers", str(n_gpu_layers)]
        else:
            cmd += ["--n-gpu-layers", "999"]

        if params.get("threads"):
            cmd += ["--threads", str(params["threads"])]
        if params.get("ctx_size"):
            cmd += ["--ctx-size", str(params["ctx_size"])]
        if params.get("batch_size"):
            cmd += ["--batch-size", str(params["batch_size"])]
        if params.get("ubatch_size"):
            cmd += ["--ubatch-size", str(params["ubatch_size"])]
        if params.get("flash_attn"):
            cmd += ["--flash-attn", "on"]
        if params.get("mlock"):
            cmd += ["--mlock"]
        if params.get("mmap") is False:
            cmd += ["--no-mmap"]
        if params.get("cache_type_k") and params.get("cache_type_k") != "auto":
            cmd += ["--cache-type-k", str(params["cache_type_k"])]
        if params.get("cache_type_v") and params.get("cache_type_v") != "auto":
            cmd += ["--cache-type-v", str(params["cache_type_v"])]
        if params.get("parallel"):
            cmd += ["--parallel", str(params["parallel"])]

        # 采样参数
        if params.get("temp") is not None:
            cmd += ["--temp", str(params["temp"])]
        if params.get("top_p") is not None:
            cmd += ["--top-p", str(params["top_p"])]
        if params.get("top_k") is not None:
            cmd += ["--top-k", str(params["top_k"])]
        if params.get("repeat_penalty") is not None:
            cmd += ["--repeat-penalty", str(params["repeat_penalty"])]
        if params.get("repeat_last_n") is not None:
            cmd += ["--repeat-last-n", str(params["repeat_last_n"])]
        if params.get("min_p") is not None:
            cmd += ["--min-p", str(params["min_p"])]
        if params.get("mirostat") is not None and int(params["mirostat"]) > 0:
            cmd += ["--mirostat", str(params["mirostat"])]
        if params.get("mirostat_lr") is not None:
            cmd += ["--mirostat-lr", str(params["mirostat_lr"])]
        if params.get("mirostat_ent") is not None:
            cmd += ["--mirostat-ent", str(params["mirostat_ent"])]
        if params.get("frequency_penalty") is not None:
            cmd += ["--frequency-penalty", str(params["frequency_penalty"])]
        if params.get("presence_penalty") is not None:
            cmd += ["--presence-penalty", str(params["presence_penalty"])]
        if params.get("rope_freq_scale") is not None:
            cmd += ["--rope-freq-scale", str(params["rope_freq_scale"])]
        if params.get("rope_freq_base") is not None:
            cmd += ["--rope-freq-base", str(params["rope_freq_base"])]
        if params.get("seed") is not None and params.get("seed", 0) != 0:
            cmd += ["--seed", str(params["seed"])]
        if params.get("max_tokens") is not None:
            cmd += ["--n-predict", str(params["max_tokens"])]
        if params.get("embeddings"):
            cmd += ["--embeddings"]
        if params.get("reranking"):
            cmd += ["--reranking"]
        if params.get("mmproj"):
            mmproj_path = params["mmproj"]
            if not os.path.isabs(mmproj_path):
                mmproj_path = os.path.join(models_dir, mmproj_path)
            cmd += ["--mmproj", mmproj_path]

        return cmd

    async def validate_model(self, model_cfg: Dict, models_dir: str) -> Dict[str, Any]:
        """验证模型文件"""
        available = model_cfg.get("available_models", [])
        if not available:
            return {"ok": False, "error": "未配置模型文件"}
        model_file = os.path.join(models_dir, available[0].get("file", ""))
        if not os.path.isfile(model_file):
            return {"ok": False, "error": f"模型文件不存在: {model_file}"}
        return {"ok": True}

    async def health_check(self, inst: ModelInstance) -> Dict[str, Any]:
        """通过 HTTP 检查 llama-server 是否响应"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{inst.port}/health",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return {"ok": resp.status == 200, "status_code": resp.status}
        except Exception as e:
            return {"ok": False, "error": str(e)}
