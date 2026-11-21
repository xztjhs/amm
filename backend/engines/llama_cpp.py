"""
llama.cpp Engine
================
GGUF 模型推理引擎，支持 Chat / Embedding / ASR / TTS / Reranker / OCR。
"""
import asyncio
import json
import logging
import os
import re
import subprocess
from typing import Dict, List, Optional, Any

import aiohttp

from backend.core.engine import BaseEngine, EngineVersion, ModelInstance

logger = logging.getLogger("AMM.Engine.LlamaCpp")

# ---------------------------------------------------------------
# 参数兼容层（v1.1）
# ---------------------------------------------------------------
# llama.cpp 在演进中改过若干 CLI 开关。引擎目录支持安装任意上游版本，
# 因此启动前要按「实际二进制的 --help」做一次能力探测，避免老参数把新版本
# 直接顶死（典型案例：b1238x 系列移除了 --mlock / --no-mmap，改为 --load-mode）。
_FLAG_CACHE: Dict[str, set] = {}

# 参数名 -> [(新写法, 需要的能力标记), ...]  按优先级排列
_FLAG_ALTERNATIVES = {
    "mlock": [("--load-mode", "mlock"), ("--mlock", None)],
    "mmap_off": [("--load-mode", "none"), ("--no-mmap", None)],
}


def _supported_flags(binary: str) -> set:
    """探测二进制支持的 --flag 列表（结果按二进制 mtime 缓存）。"""
    try:
        st = os.stat(binary)
        key = f"{binary}:{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return set()
    if key in _FLAG_CACHE:
        return _FLAG_CACHE[key]
    flags: set = set()
    try:
        proc = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=25)
        text = (proc.stdout or "") + (proc.stderr or "")
        flags = set(re.findall(r"--[a-z0-9][a-z0-9-]+", text))
    except Exception as e:
        logger.warning(f"探测 llama-server 参数失败 ({binary}): {e}")
    _FLAG_CACHE[key] = flags
    return flags


def _resolve_flag(flags: set, name: str):
    """把逻辑参数名解析成该二进制实际支持的写法。

    返回:
      []                    -> 该版本不支持，静默跳过
      ["--flag"]            -> 普通开关
      ["--load-mode","mlock"] -> 新写法带值
    """
    for alt, mode in _FLAG_ALTERNATIVES.get(name, []):
        if not flags or alt in flags:
            return [alt, mode] if mode else [alt]
    return []

LLAMACPP_VERSIONS = [
    EngineVersion(
        engine_type="llama_cpp",
        version="b4727",
        install_path="/usr/local/bin/llama-server",
        binary_path="/usr/local/bin/llama-server",
        is_default=True,
        flavor="cuda",
        metadata={
            "repo": "https://github.com/ggerganov/llama.cpp",
            "flavor": "cuda",
            "build_cmd": "cmake -B build -DGGML_CUDA=ON && cmake --build build -j$(nproc) --target llama-server",
        },
    ),
]


def _make_versions(root: str) -> List[EngineVersion]:
    """生成 llama.cpp 的 cpu / cuda 双 flavor 版本清单"""
    out = []
    for version in ("latest",):
        for flavor in ("cuda", "cpu"):
            install_path = os.path.join(root, "llama_cpp", f"{version}-{flavor}")
            out.append(EngineVersion(
                engine_type="llama_cpp",
                version=version,
                install_path=install_path,
                binary_path=os.path.join(install_path, "bin", "llama-server"),
                is_default=(flavor == "cuda"),
                flavor=flavor,
                metadata={
                    "repo": "https://github.com/ggerganov/llama.cpp",
                    "flavor": flavor,
                    "cuda_build": flavor == "cuda",
                    "build_cmd": (
                        "cmake -B build -DGGML_CUDA=ON && cmake --build build -j$(nproc) --target llama-server"
                        if flavor == "cuda" else
                        "cmake -B build -DGGML_CUDA=OFF && cmake --build build -j$(nproc) --target llama-server"
                    ),
                },
            ))
    return out


class LlamaCppEngine(BaseEngine):
    """llama.cpp 推理引擎"""

    engine_type = "llama_cpp"

    def __init__(self, engine_dir: str = "/amm/backend/engines_installed"):
        super().__init__(engine_dir)
        # BaseEngine 把 engine_dir 拼成 .../llama_cpp，上一级即引擎根目录
        self.root = str(self.engine_dir.parent)

    def get_display_name(self) -> str:
        return "llama.cpp (GGUF)"

    def get_supported_categories(self) -> List[str]:
        return ["chat", "embedding", "asr", "tts", "reranker", "ocr"]

    def get_description(self) -> str:
        return "高性能 GGUF 量化模型推理引擎，支持 CPU/GPU 混合推理，适用于 LLM / Embedding / ASR / TTS / Reranker / OCR 等文本和多模态模型。"

    async def list_installed_versions(self) -> List[EngineVersion]:
        """检查已安装版本（含 cpu / cuda 两 flavor）"""
        versions = []
        candidates = list(LLAMACPP_VERSIONS) + _make_versions(self.root)
        for v in candidates:
            if v.binary_path and os.path.isfile(v.binary_path):
                v.status = "installed"
            elif v.binary_path and os.path.isdir(os.path.dirname(v.binary_path)):
                v.status = "available"
            else:
                v.status = "available"
            versions.append(v)
        return versions

    async def get_available_versions(self) -> List[EngineVersion]:
        return await self.list_installed_versions()

    def _resolve_binary(self, inst) -> str:
        """定位 llama-server 可执行文件。

        优先级：
          1. 实例指定的 version + flavor（如 b12381-cuda）
          2. 同 flavor 下任意已安装版本（engine_version 为空时最常见）
          3. 实例指定的 version（旧目录结构 engines_installed/llama_cpp/<ver>）
          4. 镜像内置 /usr/local/bin/llama-server
        以前第 2 步缺失，固定槽位实例（engine_version 为空）会直接落到
        /usr/local/bin/llama-server，而该文件在引擎目录模式下根本不存在，
        于是 Dashboard 点 Start 就报 No such file or directory。
        """
        flavor = inst.flavor or "cuda"
        ver = (inst.engine_version or "").strip()

        def _ok(p):
            return p.is_file() and os.access(p, os.X_OK)

        # 1) 精确 version+flavor
        if ver:
            p = self.engine_dir / f"{ver}-{flavor}" / "bin" / "llama-server"
            if _ok(p):
                return str(p)

        # 2) 同 flavor 的任意已安装版本（优先最新）
        if self.engine_dir.is_dir():
            cands = sorted(
                (d for d in self.engine_dir.iterdir()
                 if d.is_dir() and d.name.endswith(f"-{flavor}")),
                reverse=True)
            for d in cands:
                b = d / "bin" / "llama-server"
                if _ok(b):
                    logger.info(f"实例未指定版本，自动选用已安装引擎: {d.name}")
                    return str(b)
            # 2b) 该 flavor 完全没有 -> 用另一个 flavor 兜底（至少能跑起来）
            for other in ("cuda", "cpu"):
                if other == flavor:
                    continue
                for d in sorted(
                        (d for d in self.engine_dir.iterdir()
                         if d.is_dir() and d.name.endswith(f"-{other}")), reverse=True):
                    b = d / "bin" / "llama-server"
                    if _ok(b):
                        logger.warning(
                            f"实例需要的 {flavor} 版本未安装，临时使用 {d.name}")
                        return str(b)

        # 3) 旧目录结构
        if ver and ver not in ("b4727", "latest"):
            p = self.engine_dir / ver / "bin" / "llama-server"
            if _ok(p):
                return str(p)

        # 4) 镜像内置
        return "/usr/local/bin/llama-server"

    @staticmethod
    def _record_resolved(inst, binary: str) -> None:
        """把实际选中的引擎 version/flavor 回填到实例。

        固定场景实例（chat/embedding/...）初始化时 engine_version/flavor 都是空，
        启动时靠 _resolve_binary 的兜底逻辑挑一个已安装的构建。
        若不回填，界面上「引擎」列就只有 `llama_cpp`，看不出到底用的
        cuda 还是 cpu 版本 —— 这对排障和确认是否真跑在 GPU 上很关键。
        """
        try:
            name = os.path.basename(os.path.dirname(os.path.dirname(binary)))
            # 目录名为 <version>-<flavor>，如 b12381-cuda
            if "-" in name and name not in ("bin",):
                ver, _, flavor = name.rpartition("-")
                if ver and flavor in ("cpu", "cuda"):
                    if not (getattr(inst, "engine_version", "") or "").strip():
                        inst.engine_version = ver
                    if not (getattr(inst, "flavor", "") or "").strip():
                        inst.flavor = flavor
                    return
            # 镜像内置 /usr/local/bin/llama-server：无法判定构建，标记为 builtin
            if binary.startswith("/usr/local/bin/"):
                if not (getattr(inst, "engine_version", "") or "").strip():
                    inst.engine_version = "builtin"
        except Exception:
            pass

    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """构建 llama-server 命令行"""
        server_bin = self._resolve_binary(inst)
        self._record_resolved(inst, server_bin)

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
        flags = _supported_flags(server_bin)

        if params.get("flash_attn"):
            # b1238x 起 --flash-attn 只接受 on/off/auto，旧版是裸开关
            cmd += ["--flash-attn", "on"] if (not flags or "--flash-attn" in flags) else []
        if params.get("mlock"):
            cmd += _resolve_flag(flags, "mlock")
        if params.get("mmap") is False:
            cmd += _resolve_flag(flags, "mmap_off")
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
        if params.get("reasoning_budget_enabled") and params.get("reasoning_budget"):
            cmd += ["--reasoning-budget", str(params["reasoning_budget"])]
        # 禁用思考/推理输出：本项目 llama.cpp(b4727) 不识别 --no-reasoning，用 --reasoning off 实现同等效果
        if params.get("no_reasoning"):
            cmd += ["--reasoning", "off"]
        # 类别必需的能力开关：不能只信 parameters。
        # llama-server 少 --embeddings / --reranking 时会直接回 501，
        # 而参数可能来自旧实例、模板默认值或手工填错，任何入口都可能漏。
        # 这里以 category 为准强制补齐，保证「选对类别 = 一定能用」。
        inst_category = (getattr(inst, "category", "") or category or "").lower()
        if params.get("embeddings") or inst_category == "embedding":
            cmd += ["--embeddings"]
        if params.get("reranking") or inst_category == "reranker":
            cmd += ["--reranking"]
        if params.get("mmproj"):
            mmproj_path = params["mmproj"]
            if not os.path.isabs(mmproj_path):
                mmproj_path = os.path.join(models_dir, mmproj_path)
            cmd += ["--mmproj", mmproj_path]
        elif inst_category in ("ocr", "image", "video"):
            # 视觉类类别没有 mmproj 就只是个纯文本模型，推理时会报
            # "image input is not supported" 或直接忽略图片。这里给出
            # 明确日志，提示用户去参数里补 mmproj。
            logger.warning(
                f"实例 {getattr(inst, 'model_id', '?')} 类别为 {inst_category} "
                f"但未配置 mmproj，图片输入将不可用")

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
