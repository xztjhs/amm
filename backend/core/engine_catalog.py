"""
AMM Engine Catalog (v1.1)
=========================
统一的「引擎版本目录」：上游版本查询 + 本地安装扫描 + 下载 / 安装 / 卸载任务。

版本来源
--------
llama.cpp               GitHub Releases  (tag 形如 b12377)
vLLM                    PyPI JSON        (vllm)
Diffusers               PyPI JSON        (diffusers)
sentence-transformers   PyPI JSON        (sentence-transformers)

flavor 规则
-----------
llama_cpp              cpu / cuda  （两套编译产物，必须区分）
sentence_transformers  cpu / cuda  （两套 venv，cpu 不占显存）
vllm                   cuda only
diffusers              cuda only

磁盘布局（与 VersionManager 一致）
---------------------------------
$AMM_ENGINES_ROOT/<engine>/<version>-<flavor>/
    llama_cpp : src/  build-<flavor>/  bin/llama-server
    pip-venv  : venv/  bin/<cli>  wheels/ (可选, 仅下载)
"""
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("AMM.EngineCatalog")

AMM_ROOT = os.environ.get("AMM_ROOT", "/amm")
DATA_DIR = os.environ.get("AMM_DATA_DIR", os.path.join(AMM_ROOT, "data"))
ENGINES_ROOT = os.environ.get("AMM_ENGINES_ROOT", os.path.join(AMM_ROOT, "backend", "engines_installed"))
_CACHE_PATH = os.path.join(DATA_DIR, "engine_catalog_cache.json")
_TASKS_PATH = os.path.join(DATA_DIR, "engine_tasks.json")

# 目录体积缓存: path -> (mtime, size_mb)。避免每次请求都 rglob 扫 19 万文件（D3）
_SIZE_CACHE: Dict[str, tuple] = {}

CACHE_TTL = 30 * 60          # 上游版本缓存 30 分钟

# ---- PyPI 镜像源（可在 Settings 里改，配置存 data/pip_settings.json）----
PIP_PRESETS = {
    "aliyun":  {"label": "阿里云（推荐）",   "url": "https://mirrors.aliyun.com/pypi/simple/"},
    "tsinghua": {"label": "清华大学",        "url": "https://pypi.tuna.tsinghua.edu.cn/simple"},
    "ustc":    {"label": "中科大",            "url": "https://mirrors.ustc.edu.cn/pypi/simple"},
    "tencent": {"label": "腾讯云",            "url": "https://mirrors.cloud.tencent.com/pypi/simple"},
    "huawei":  {"label": "华为云",            "url": "https://repo.huaweicloud.com/repository/pypi/simple"},
    "official": {"label": "PyPI 官方（可能较慢）", "url": "https://pypi.org/simple"},
}
_PIP_SETTINGS_PATH = os.path.join(DATA_DIR, "pip_settings.json")
_DEFAULT_PIP_INDEX = PIP_PRESETS["aliyun"]["url"]


def get_pip_index() -> str:
    """当前生效的 PyPI 索引（配置优先，其次默认阿里云）"""
    try:
        with open(_PIP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            v = (json.load(f) or {}).get("index_url") or ""
        if v.strip():
            return v.strip()
    except Exception:
        pass
    return _DEFAULT_PIP_INDEX


def set_pip_index(url: str) -> str:
    """写入 PyPI 索引配置；空字符串表示恢复默认"""
    os.makedirs(DATA_DIR, exist_ok=True)
    url = (url or "").strip()
    with open(_PIP_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"index_url": url}, f, ensure_ascii=False, indent=2)
    return url or _DEFAULT_PIP_INDEX


PYPI_INDEX = _DEFAULT_PIP_INDEX
PIP_MIRROR_HOST = _DEFAULT_PIP_INDEX.rstrip("/")
TORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    # PyTorch 官方没有 cu130 的 wheel（截至 2026-11 最高 cu128）。
    # cu128 向下兼容 CUDA 12.x，且能在 CUDA 13 驱动上跑（驱动向后兼容），
    # 所以容器虽然是 CUDA 13.2，torch 仍装 cu128。
    "cuda": os.environ.get("AMM_TORCH_CUDA_INDEX",
                           "https://download.pytorch.org/whl/cu128"),
}

# 精简镜像（如 amm:rocky9-cu132）只装 cudart/nvcc/nvrtc/nvml，没有 cuBLAS/cuDNN 等
# 推理所需运行时。torch cu128 需要它们，这里用 pip 版 CUDA 运行时补齐。
# 版本是实测能跑通的组合：nes 12.9 系列 + torch 2.8.0。
TORCH_CUDA_RUNTIME_PKGS = [
    "nvidia-cublas-cu12==12.9.3.13",
    "nvidia-cuda-runtime-cu12==12.9.79",
    "nvidia-cuda-nvrtc-cu12==12.9.86",
    "nvidia-cuda-cupti-cu12==12.9.79",
    "nvidia-cudnn-cu12==9.10.2.21",
    "nvidia-cufft-cu12==11.4.1.4",
    "nvidia-cufile-cu12==1.14.1.1",
    "nvidia-curand-cu12==10.3.10.19",
    "nvidia-cusolver-cu12==11.7.5.82",
    "nvidia-cusparse-cu12==12.5.10.65",
    "nvidia-cusparselt-cu12==0.8.1",
    "nvidia-nccl-cu12==2.32.6",
    "nvidia-nvjitlink-cu12==12.9.86",
    "nvidia-nvtx-cu12==12.8.90",
]

ENGINE_META: Dict[str, Dict[str, Any]] = {
    "llama_cpp": {
        "display_name": "llama.cpp (GGUF)",
        "kind": "source-build",
        "flavors": ["cuda", "cpu"],
        "default_flavor": "cuda",
        "upstream": "github",
        "repo": "ggml-org/llama.cpp",
        "homepage": "https://github.com/ggml-org/llama.cpp",
        "supports": ["chat", "embedding", "asr", "tts", "reranker", "ocr"],
        "binary": "bin/llama-server",
        "note": "从源码编译；cpu/cuda 产物不同，必须分别安装",
    },
    "vllm": {
        "display_name": "vLLM",
        "kind": "pip-venv",
        "flavors": ["cuda"],
        "default_flavor": "cuda",
        "upstream": "pypi",
        "pkg": "vllm",
        "homepage": "https://pypi.org/project/vllm/",
        "supports": ["chat", "embedding"],
        "binary": "bin/vllm",
        "note": "仅 CUDA；独立 venv，可多版本共存",
    },
    "diffusers": {
        "display_name": "Diffusers (HuggingFace)",
        "kind": "pip-venv",
        "flavors": ["cuda"],
        "default_flavor": "cuda",
        "upstream": "pypi",
        "pkg": "diffusers",
        "homepage": "https://pypi.org/project/diffusers/",
        "supports": ["image", "video"],
        "binary": "venv/bin/python",
        "note": "仅 CUDA；文生图 / 视频",
    },
    "sentence_transformers": {
        "display_name": "sentence-transformers",
        "kind": "pip-venv",
        "flavors": ["cuda", "cpu"],
        "default_flavor": "cuda",
        "upstream": "pypi",
        "pkg": "sentence-transformers",
        "homepage": "https://pypi.org/project/sentence-transformers/",
        "supports": ["embedding", "reranker"],
        "binary": "venv/bin/python",
        "note": "embedding / reranker 专用；cpu flavor 不占显存",
    },
}

# 上游不可达时的兜底清单（保证页面不白屏）
FALLBACK_VERSIONS: Dict[str, List[str]] = {
    # llama.cpp 的 b#### 构建全部是 GitHub prerelease，正式版只发 vX.Y.Z。
    # 兜底清单同步只留正式版，避免又冒出几十个 b12475。
    "llama_cpp": ["v1.0.0", "v0.9.0", "v0.8.0"],
    "vllm": ["0.22.1", "0.8.5", "0.7.3"],
    "diffusers": ["0.39.0", "0.36.0", "0.35.2"],
    "sentence_transformers": ["3.3.1", "3.0.1"],
}

# llama.cpp 的每次 CI 构建（b12377 之类）在 GitHub 上都标 pristine=prerelease。
# 页面上几十个 b#### 全是 pre 版，用户挑不动，这里默认只保留官方正式版 vX.Y.Z。
# 需要装 nightly 时把环境变量 AMM_ENGINE_INCLUDE_PRERELEASE=1 打开即可。
def _include_prerelease() -> bool:
    return str(os.environ.get("AMM_ENGINE_INCLUDE_PRERELEASE", "0")).strip().lower() in (
        "1", "true", "yes", "on")

# 正式版判定：v1.0.0 / 1.0.0 这类；b#### / *-rc* / *-pre* 一律视为 pre
_STABLE_VER_RE = re.compile(r"^v?\d+\.\d+(\.\d+)?$")


def _is_stable_version(ver: str) -> bool:
    v = (ver or "").strip()
    if not v or not _STABLE_VER_RE.match(v):
        return False
    return True

_SAFE_VER = re.compile(r"^[A-Za-z0-9._+-]+$")


# ============================================================
# 缓存
# ============================================================
def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(path: str, data: Any):
    _ensure_data_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ============================================================
# 上游版本查询
# ============================================================
async def _fetch_json(url: str, timeout: int = 20) -> Any:
    headers = {"User-Agent": "AMM-EngineCatalog/1.1"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if "api.github.com" in url and token:
        headers["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.get(url, headers=headers) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} {url}")
            return await r.json()


async def _upstream_github(repo: str, limit: int = 5,
                           max_pages: int = 3) -> List[Dict[str, Any]]:
    """抓 GitHub Releases。

    llama.cpp 每天发若干个 b##### CI 构建，全都标成 prerelease；
    只取第一页（最新 25 条）会全是 pre 版，正式版 vX.Y.Z 被挤到后面几页。
    这里逐页翻，直到凑够 limit 条正式版或翻完 max_pages 页。
    """
    out: List[Dict[str, Any]] = []
    stable_count = 0
    for page in range(1, max_pages + 1):
        url = (f"https://api.github.com/repos/{repo}/releases"
               f"?per_page=100&page={page}")
        data = await _fetch_json(url)
        if not data:
            break
        for rel in data:
            tag = (rel.get("tag_name") or "").strip()
            if not tag:
                continue
            is_pre = bool(rel.get("prerelease"))
            out.append({
                "version": tag,
                "published_at": rel.get("published_at") or "",
                "name": rel.get("name") or tag,
                "prerelease": is_pre,
                "url": rel.get("html_url") or "",
                "note": "源码编译（cpu/cuda 各一份产物）",
            })
            if not is_pre and _is_stable_version(tag):
                stable_count += 1
        # 已经拿到足够正式版就不用继续翻页（省 API 配额）
        if stable_count >= limit or len(data) < 100:
            break
    return out


def _filter_stable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉 pre 版，只留官方正式大版本（vX.Y.Z）。

    若仓库压根没有正式版（极少数情况），退回最新若干条，避免页面空白。
    """
    if _include_prerelease():
        return rows
    stable = [r for r in rows
              if not r.get("prerelease") and _is_stable_version(r.get("version", ""))]
    if stable:
        return stable
    # 一条正式版都没有时，宁可不显示上游版本（前端只列本地已安装），
    # 也不要再冒出几十个 b##### 把页面刷满。
    return []


def _py_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _requires_ok(spec: str) -> bool:
    """判断 requires_python 是否接受当前解释器（只处理常见 >= / < 组合）。"""
    if not spec:
        return True
    cur = sys.version_info[:3]
    for part in spec.split(","):
        part = part.strip()
        m = re.match(r"(>=|<=|>|<|==|!=)\s*([0-9.]+)", part)
        if not m:
            continue
        op, ver = m.group(1), _py_tuple(m.group(2))
        ver = tuple(list(ver) + [0] * (3 - len(ver)))
        if op == ">=" and not cur >= ver:
            return False
        if op == ">" and not cur > ver:
            return False
        if op == "<=" and not cur <= ver:
            return False
        if op == "<" and not cur < ver:
            return False
    return True


async def _upstream_pypi(pkg: str) -> List[Dict[str, Any]]:
    data = await _fetch_json(f"https://pypi.org/pypi/{pkg}/json")
    releases = (data or {}).get("releases") or {}
    rows = []
    for ver, files in releases.items():
        if not files:
            continue
        if re.search(r"(a|b|rc|dev)", ver):
            continue
        rp = ""
        for f in files:
            rp = f.get("requires_python") or rp
        ts = max((f.get("upload_time_iso_8601") or "") for f in files)
        rows.append({
            "version": ver, "published_at": ts,
            "name": f"{pkg}=={ver}",
            "url": f"https://pypi.org/project/{pkg}/{ver}/",
            "requires_python": rp,
            "py_ok": _requires_ok(rp),
        })
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return rows[:40]


def _version_key(v: str):
    parts = re.split(r"[.\-+_]", v)
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return out


async def upstream_versions(engine_type: str, refresh: bool = False) -> Dict[str, Any]:
    """查询上游版本（带 30 分钟缓存）"""
    meta = ENGINE_META.get(engine_type)
    if not meta:
        raise ValueError(f"未知引擎: {engine_type}")

    cache = _read_json(_CACHE_PATH)
    hit = cache.get(engine_type) or {}
    if not refresh and hit.get("ts") and time.time() - hit["ts"] < CACHE_TTL and hit.get("versions"):
        # 缓存可能是过滤规则生效前写入的（几十个 b####），读出来统一再过一遍
        return {"source": meta["upstream"], "cached": True, "fetched_at": hit["ts"],
                "versions": _filter_stable(hit["versions"])}

    try:
        if meta["upstream"] == "github":
            versions = _filter_stable(await _upstream_github(meta["repo"]))
        else:
            versions = await _upstream_pypi(meta["pkg"])
        if not versions:
            raise RuntimeError("上游返回空列表")
        cache[engine_type] = {"ts": time.time(), "versions": versions}
        _write_json(_CACHE_PATH, cache)
        return {"source": meta["upstream"], "cached": False, "fetched_at": cache[engine_type]["ts"], "versions": versions}
    except Exception as e:
        logger.warning(f"[catalog] 查询 {engine_type} 上游版本失败: {e}")
        fallback = [{"version": v, "published_at": "", "name": v, "url": meta.get("homepage", ""), "note": "内置兜底清单"}
                    for v in FALLBACK_VERSIONS.get(engine_type, [])]
        # 缓存里可能是过滤前的旧数据（几十个 b####），命中时同样要过滤一遍
        cached_versions = _filter_stable(hit.get("versions") or [])
        return {
            "source": meta["upstream"],
            "cached": bool(cached_versions),
            "error": str(e)[:300],
            "versions": cached_versions or fallback,
        }


# ============================================================
# 本地安装扫描
# ============================================================
def _engine_dir(engine_type: str) -> Path:
    return Path(ENGINES_ROOT) / engine_type


def _parse_dir_name(name: str, flavors: List[str]):
    for fl in flavors:
        suffix = f"-{fl}"
        if name.endswith(suffix):
            return name[: -len(suffix)], fl
    return name, ""


def _dir_ok(engine_type: str, path: Path) -> bool:
    meta = ENGINE_META.get(engine_type) or {}
    rel = meta.get("binary") or ""
    if not rel:
        return False
    target = path / rel
    if target.is_file():
        return True
    # 旧的全局二进制（镜像内置 llama-server）
    return False


def _dir_size_mb_cached(entry: Path) -> float:
    """目录体积统计（带缓存）。

    原来是每次调用都 rglob 扫全目录，vLLM/diffusers 的 venv 合计 ~19 万文件，
    单次耗时 7-13 秒，而启动向导每渲染一次就调一次（缺陷 D3）。
    缓存键 = 目录路径 + mtime：目录被改过（新装/卸载）才会重算。
    """
    key = str(entry)
    try:
        mtime = entry.stat().st_mtime
    except OSError:
        return 0.0
    hit = _SIZE_CACHE.get(key)
    if hit and abs(hit[0] - mtime) < 1e-6:
        return hit[1]
    total = 0
    try:
        for p in entry.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except Exception:
        pass
    size = round(total / (1024 * 1024), 1)
    _SIZE_CACHE[key] = (mtime, size)
    return size


def installed_versions(engine_type: str) -> List[Dict[str, Any]]:
    meta = ENGINE_META.get(engine_type) or {}
    flavors = meta.get("flavors") or [""]
    root = _engine_dir(engine_type)
    out = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        ver, flavor = _parse_dir_name(entry.name, [f for f in flavors if f])
        if not ver:
            continue
        size_mb = _dir_size_mb_cached(entry)
        out.append({
            "version": ver,
            "flavor": flavor,
            "path": str(entry),
            "install_path": str(entry),
            "status": "installed" if _dir_ok(engine_type, entry) else "incomplete",
            "binary_path": str(entry / (meta.get("binary") or "")),
            "size_mb": size_mb,
            "installed_at": _dir_mtime(entry),
        })
    # 镜像内置二进制（如 /usr/local/bin/llama-server）视为已安装的 latest-cuda
    if engine_type == "llama_cpp":
        builtin = Path("/usr/local/bin/llama-server")
        if builtin.is_file() and not any(v["version"] == "latest" for v in out):
            out.insert(0, {
                "version": "latest",
                "flavor": "cuda",
                "path": str(builtin),
                "install_path": str(builtin),
                "status": "installed",
                "binary_path": str(builtin),
                "size_mb": round(builtin.stat().st_size / (1024 * 1024), 1),
                "installed_at": int(builtin.stat().st_mtime),
                "builtin": True,
            })
    return out


def _dir_mtime(p: Path) -> int:
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return 0


async def catalog(refresh: bool = False) -> Dict[str, Any]:
    """完整目录：引擎元信息 + 上游版本 + 本地安装"""
    result = {}
    for engine_type, meta in ENGINE_META.items():
        up = await upstream_versions(engine_type, refresh=refresh)
        inst = installed_versions(engine_type)
        installed_keys = {(v["version"], v["flavor"]) for v in inst}
        upstream_rows = []
        for row in up.get("versions", []):
            variants = []
            for fl in meta["flavors"]:
                variants.append({
                    "flavor": fl,
                    "installed": (row["version"], fl) in installed_keys,
                })
            upstream_rows.append({**row, "variants": variants})
        # 本地已安装但不在（过滤后）上游清单里的版本也要能看到，
        # 否则只留正式版之后，用户装的 b12381 之类就没法在页面上卸载/清理了。
        upstream_versions_set = {r["version"] for r in upstream_rows}
        for local in inst:
            if local["version"] in upstream_versions_set:
                continue
            upstream_rows.append({
                "version": local["version"],
                "published_at": "",
                "name": local["version"],
                "prerelease": True,
                "url": meta.get("homepage", ""),
                "note": "本地已安装（非上游正式版，可卸载）",
                "local_only": True,
                "variants": [{"flavor": local.get("flavor") or meta["default_flavor"],
                              "installed": True}],
            })
            upstream_versions_set.add(local["version"])
        result[engine_type] = {
            **{k: meta[k] for k in ("display_name", "kind", "flavors", "default_flavor", "supports", "homepage", "note")},
            "upstream_source": up.get("source"),
            "upstream_cached": up.get("cached", False),
            "upstream_error": up.get("error", ""),
            "upstream": upstream_rows,
            "installed": inst,
            "installed_count": len(inst),
        }
    return {"engines": result, "engines_root": ENGINES_ROOT}


# ============================================================
# 安装任务
# ============================================================
@dataclass
class EngineTask:
    task_id: str
    engine_type: str
    version: str
    flavor: str
    action: str                 # download | install | uninstall
    status: str = "pending"     # pending | running | done | failed
    progress: float = 0.0
    message: str = ""
    log: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: str = ""

    def to_dict(self, tail: int = 40):
        return {
            "task_id": self.task_id,
            "engine_type": self.engine_type,
            "version": self.version,
            "flavor": self.flavor,
            "action": self.action,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "log": self.log[-tail:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


_TASKS: Dict[str, EngineTask] = {}
_RUNNING: Dict[str, asyncio.Task] = {}


def list_tasks() -> List[Dict[str, Any]]:
    return [t.to_dict() for t in sorted(_TASKS.values(), key=lambda x: x.started_at, reverse=True)][:30]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    t = _TASKS.get(task_id)
    return t.to_dict() if t else None


def find_active(engine_type: str, version: str, flavor: str, action: str) -> Optional[Dict[str, Any]]:
    for t in _TASKS.values():
        if (t.engine_type, t.version, t.flavor, t.action) == (engine_type, version, flavor, action) and t.status in ("pending", "running"):
            return t.to_dict()
    return None


def _log(task: EngineTask, line: str, progress: Optional[float] = None):
    ts = time.strftime("%H:%M:%S")
    task.log.append(f"[{ts}] {line}")
    task.message = line
    if progress is not None:
        task.progress = max(0.0, min(1.0, progress))
    if len(task.log) > 400:
        del task.log[:-400]
    logger.info(f"[engine-task {task.task_id}] {line}")


async def _run(cmd: List[str], task: EngineTask, label: str, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
    _log(task, f"$ {' '.join(cmd[:8])}{' …' if len(cmd) > 8 else ''}")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    tail: List[str] = []
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "ignore").rstrip()
        if not line:
            continue
        tail.append(line)
        if len(tail) > 6:
            tail.pop(0)
        task.log.append(f"    {line[:400]}")
        task.message = f"{label}: {line[:200]}"
    rc = await proc.wait()
    if len(task.log) > 400:
        del task.log[:-400]
    if rc != 0:
        raise RuntimeError(f"{label} 失败 (rc={rc}): " + " | ".join(tail[-3:])[:600])


def _task_dir(engine_type: str, version: str, flavor: str) -> Path:
    name = f"{version}-{flavor}" if flavor else version
    return _engine_dir(engine_type) / name


def _find_reusable_llama_src(ver: str, exclude_flavor: str) -> str:
    """找同版本、其它 flavor 已克隆好的 llama.cpp 源码目录。"""
    root = Path(os.environ.get("AMM_ENGINES_ROOT", "/opt/amm-engines")) / "llama_cpp"
    if not root.is_dir():
        return ""
    for flavor in ("cpu", "cuda"):
        if flavor == exclude_flavor:
            continue
        cand = root / f"{ver}-{flavor}" / "src"
        if (cand / ".git").is_dir() and (cand / "CMakeLists.txt").is_file():
            return str(cand)
    return ""


async def _download_llama(task: EngineTask, ver: str):
    d = _task_dir("llama_cpp", ver, task.flavor)
    src = d / "src"
    d.mkdir(parents=True, exist_ok=True)
    _log(task, f"拉取 llama.cpp 源码 {ver}（浅克隆）", 0.05)
    if (src / ".git").is_dir():
        _log(task, "源码已存在，尝试更新", 0.1)
        try:
            await _run(["git", "-C", str(src), "fetch", "--depth", "1", "origin", f"refs/tags/{ver}:refs/tags/{ver}"], task, "git fetch")
        except Exception as e:
            _log(task, f"更新失败，沿用现有源码: {e}")
    else:
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        # 先找同一版本其它 flavor 已克隆好的源码，直接复用（省一次整仓下载，
        # 也绕开 github 偶发的 "OpenSSL SSL_read: unexpected eof"）。
        reused = _find_reusable_llama_src(ver, task.flavor)
        if reused:
            _log(task, f"复用已下载源码: {reused}", 0.1)
            try:
                shutil.copytree(reused, src, symlinks=True)
            except Exception as e:
                _log(task, f"复用失败（{e}），改为重新克隆")
                reused = None
        if not reused:
            last_err = None
            for attempt in range(1, 4):
                try:
                    await _run(["git", "clone", "--depth", "1", "--branch", ver,
                                "https://github.com/ggml-org/llama.cpp.git", str(src)],
                               task, f"git clone (try {attempt})", cwd=str(d))
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    shutil.rmtree(src, ignore_errors=True)
                    _log(task, f"clone 第 {attempt} 次失败，重试: {str(e)[:120]}")
                    await asyncio.sleep(3)
            if last_err:
                raise last_err
    _log(task, f"源码就绪: {src}", 0.3)


async def _download_pip(task: EngineTask, pkg: str, ver: str):
    d = _task_dir(task.engine_type, ver, task.flavor)
    wheels = d / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    py = shutil.which("python3") or "python3"
    _log(task, f"下载 wheel: {pkg}=={ver} → {wheels}", 0.05)
    args = [py, "-m", "pip", "download", f"{pkg}=={ver}", "-d", str(wheels), "-i", get_pip_index()]
    if task.engine_type in ("diffusers", "sentence_transformers"):
        # 需要 torch，按 flavor 指定索引
        args += ["--extra-index-url", TORCH_INDEX.get(task.flavor, TORCH_INDEX["cuda"])]
    await _run(args, task, "pip download")
    _log(task, "wheel 下载完成（安装时会复用本地缓存）", 0.3)


def _cuda_nvcc() -> str:
    """定位 nvcc。"""
    for p in ("/usr/local/cuda/bin/nvcc",):
        if os.path.isfile(p):
            return p
    import glob
    cands = sorted(glob.glob("/usr/local/cuda-*/bin/nvcc"), reverse=True)
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which("nvcc") or ""


def _detect_cuda_arch() -> str:
    """按 GPU 计算能力返回 CMAKE_CUDA_ARCHITECTURES 值，失败返回空串。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        caps = [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]
        arches = sorted({c.replace(".", "") for c in caps if "." in c})
        return ";".join(arches)
    except Exception:
        return ""


# NVIDIA 官方 rpm 源（RHEL9 / Rocky9 通用）
_CUDA_RPM_BASE = "https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64"


def _cuda_version_from_prefix() -> str:
    """从 /usr/local/cuda 反查 CUDA 主次版本，如 (13, 2)。失败返回空。"""
    import glob
    for p in sorted(glob.glob("/usr/local/cuda-*"), reverse=True):
        m = re.search(r"cuda-(\d+)\.(\d+)$", p)
        if m and os.path.isdir(p):
            return f"{m.group(1)}-{m.group(2)}"
    try:
        real = os.path.realpath("/usr/local/cuda")
        m = re.search(r"cuda-(\d+)\.(\d+)", real)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    except Exception:
        pass
    return ""


async def _find_latest_cuda_rpm(pkg_prefix: str, ver_dash: str) -> str:
    """在 NVIDIA 源目录里找指定包的最新文件名。

    pkg_prefix 形如 cuda-nvcc / cuda-cudart-devel，ver_dash 形如 13-2。
    """
    index = "/tmp/amm_nvidia_index.html"
    try:
        if not os.path.isfile(index) or time.time() - os.path.getmtime(index) > 3600:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-fsSL", "--http1.1", "--retry", "3",
                "-o", index, _CUDA_RPM_BASE + "/",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        with open(index, "r", errors="ignore") as f:
            html = f.read()
    except Exception as e:
        raise RuntimeError(f"获取 NVIDIA rpm 索引失败: {e}") from e

    pat = re.compile(
        re.escape(pkg_prefix) + r"-" + re.escape(ver_dash) + r"-([0-9][0-9.]*)-1\.x86_64\.rpm")
    cands = sorted(set(pat.findall(html)), key=lambda v: [int(x) for x in v.split(".")])
    if not cands:
        raise RuntimeError(f"NVIDIA 源里没找到 {pkg_prefix}-{ver_dash} 的 rpm")
    return f"{pkg_prefix}-{ver_dash}-{cands[-1]}-1.x86_64.rpm"


async def _install_cuda_nvcc(task: "EngineTask"):
    """安装 cuda-nvcc（编译器本体）。rpm 解包可能因目录冲突失败，故用 rpm2cpio 兜底。"""
    ver_dash = _cuda_version_from_prefix()
    if not ver_dash:
        raise RuntimeError("无法从 /usr/local/cuda-* 推断 CUDA 版本，不能自动安装 nvcc")
    fn = await _find_latest_cuda_rpm("cuda-nvcc", ver_dash)
    url = f"{_CUDA_RPM_BASE}/{fn}"
    tmp = "/tmp/amm_cuda_nvcc.rpm"
    _log(task, f"下载 {url}")
    await _run(["curl", "-fsSL", "--http1.1", "--retry", "3", "-C", "-", "-o", tmp, url],
               task, "download cuda-nvcc")
    # 先常规安装；目录冲突时退回解包拷贝（精简镜像里 include 常是挂载点/已存在目录）
    try:
        await _run(["rpm", "-ivh", "--nodeps", "--replacefiles", tmp], task, "install cuda-nvcc")
    except Exception as e:
        _log(task, f"rpm 安装失败（{str(e)[:120]}），改用解包方式补齐 nvcc")
        await _extract_rpm_into_root(tmp, task)
    if not _cuda_nvcc():
        raise RuntimeError("安装后仍找不到 nvcc，请检查 CUDA 安装")
    _log(task, f"nvcc 就绪: {_cuda_nvcc()}")


async def _extract_rpm_into_root(rpm_path: str, task: "EngineTask"):
    """rpm2cpio + cpio 解包，把文件按原路径落到根目录（不覆盖已有文件）。"""
    cpio_path = "/tmp/amm_rpm.cpio"
    extract_dir = "/tmp/amm_rpm_extract"
    await _run(["sh", "-c", f"rpm2cpio {rpm_path} > {cpio_path}"], task, "rpm2cpio")
    await _run(["sh", "-c", f"rm -rf {extract_dir} && mkdir -p {extract_dir} && cd {extract_dir} && cpio -idm < {cpio_path}"],
               task, "extract rpm")
    # 逐项复制：目标是绝对路径树，已存在的文件一律不覆盖（保留镜像原有运行时）
    await _run(["sh", "-c",
                f"cd {extract_dir} && find . -type f -o -type l | while read f; do "
                f"d=\"/${{f#./}}\"; mkdir -p \\\"$(dirname \\\"$d\\\")\\\"; "
                f"[ -e \\\"$d\\\" ] || cp -a \\\"$f\\\" \\\"$d\\\"; done"],
               task, "install extracted files")


async def _ensure_cudart_devel(task: "EngineTask", cuda_root: str):
    """补齐 cudart 静态库 + targets 头文件目录（nvcc 链接/编译必需）。

    只做存在性检查，全部齐了就立刻返回，不会拖慢正常环境。
    """
    tgt = os.path.join(cuda_root, "targets", "x86_64-linux")
    need_libs = [os.path.join(cuda_root, "lib64", "libcudart_static.a"),
                 os.path.join(cuda_root, "lib64", "libcudadevrt.a")]
    need_hdr = os.path.join(tgt, "include", "cuda_runtime.h")
    if all(os.path.exists(p) for p in need_libs) and os.path.exists(need_hdr):
        return

    ver_dash = _cuda_version_from_prefix()
    if not ver_dash:
        _log(task, "⚠️ 缺 cudart 静态库/头文件，且无法推断 CUDA 版本，跳过自动补齐")
        return
    _log(task, "⚠️ CUDA 工具链缺 cudart 静态库或 targets 头文件，自动补齐")
    try:
        fn = await _find_latest_cuda_rpm("cuda-cudart-devel", ver_dash)
        url = f"{_CUDA_RPM_BASE}/{fn}"
        tmp = "/tmp/amm_cuda_cudart_devel.rpm"
        await _run(["curl", "-fsSL", "--http1.1", "--retry", "3", "-C", "-", "-o", tmp, url],
                   task, "download cuda-cudart-devel")
        try:
            await _run(["rpm", "-ivh", "--nodeps", "--replacefiles", tmp],
                       task, "install cuda-cudart-devel")
        except Exception as e:
            _log(task, f"rpm 安装失败（{str(e)[:100]}），改用解包补齐")
            await _extract_rpm_into_root(tmp, task)
    except Exception as e:
        _log(task, f"⚠️ 自动补齐 cudart-devel 失败：{str(e)[:150]}")

    # rpm 常把库装到 targets/x86_64-linux/lib 而不是 lib64；
    # nvcc 两处都会找，这里做成互链，任一布局都能用。
    try:
        for name in ("libcudart_static.a", "libcudadevrt.a", "libcudart.so"):
            srcs = [os.path.join(cuda_root, "lib64", name),
                    os.path.join(tgt, "lib", name)]
            found = next((p for p in srcs if os.path.exists(p)), None)
            if not found:
                continue
            for d in (os.path.join(cuda_root, "lib64"), os.path.join(tgt, "lib")):
                os.makedirs(d, exist_ok=True)
                link = os.path.join(d, name)
                if not os.path.lexists(link):
                    os.symlink(found, link)
        # targets/include 下缺的头/子目录，从 cuda_root/include 补软链
        inc_dst = os.path.join(tgt, "include")
        inc_src = os.path.join(cuda_root, "include")
        if os.path.isdir(inc_src):
            os.makedirs(inc_dst, exist_ok=True)
            for item in os.listdir(inc_src):
                dst = os.path.join(inc_dst, item)
                src = os.path.join(inc_src, item)
                if os.path.isdir(dst) and not os.listdir(dst):
                    os.rmdir(dst)
                if not os.path.lexists(dst):
                    os.symlink(src, dst)
    except Exception as e:
        _log(task, f"⚠️ 整理 CUDA 目录布局失败：{str(e)[:120]}")


async def _ensure_cuda_toolchain(task: "EngineTask"):
    """CUDA 编译前置检查。

    实测坑：镜像只装了 cuda-nvcc 而没装 libnvvm，nvcc 调 cicc 时直接
    "/usr/local/cuda-13.2/nvvm/bin/cicc: No such file"，cmake 就报
    "CMAKE_DETERMINE_COMPILER_ID" 失败，整个安装退出。
    这里先探测 cicc，缺了自动补装 libnvvm。

    2026-11-18 补充：精简镜像常常只有 CUDA **运行时**（cudart + cublas），
    没有编译器本体，也没装 cudart-devel 的静态库。表现为：
      - 找不到 nvcc                     → 无法开始编译
      - 缺 libcudart_static.a/cudadevrt → nvcc 链接阶段 "cannot find -lcudart_static"
      - 缺 targets/x86_64-linux/include → "fatal error: cuda_runtime.h"
    这三样都在 NVIDIA 官方 rpm 里，这里按需自动补齐。
    """
    nvcc = _cuda_nvcc()
    if not nvcc:
        _log(task, "未找到 nvcc，尝试从 NVIDIA 官方源自动安装")
        await _install_cuda_nvcc(task)
        nvcc = _cuda_nvcc()
        if not nvcc:
            raise RuntimeError(
                "未找到 nvcc，无法编译 CUDA 版 llama.cpp。"
                "请在镜像里安装 CUDA toolkit（cuda-nvcc + libnvvm），或改用 cpu flavor。")

    root = os.path.dirname(os.path.dirname(nvcc))
    await _ensure_cudart_devel(task, root)

    cicc = os.path.join(root, "nvvm", "bin", "cicc")
    if os.path.isfile(cicc):
        _log(task, f"CUDA 工具链就绪（nvcc={nvcc}）")
        return

    _log(task, f"⚠️ 检测到 CUDA 工具链不完整（缺 {cicc}），尝试自动补装 libnvvm")
    # 推断 CUDA 主版本，如 13.2 -> 13-2
    import re as _re
    m = _re.search(r"cuda-(\d+)\.(\d+)", root)
    if not m:
        raise RuntimeError(
            f"CUDA 工具链缺少 nvvm/bin/cicc，且无法推断版本（{root}）。"
            "请手动安装 libnvvm 或改用 cpu flavor。")
    ver_dash = f"{m.group(1)}-{m.group(2)}"
    full = f"{m.group(1)}.{m.group(2)}"
    # 从已装 rpm 里取精确版本
    pkg_ver = ""
    try:
        q = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}", f"cuda-nvcc-{ver_dash}"],
                           capture_output=True, text=True, timeout=15)
        pkg_ver = (q.stdout or "").strip()
    except Exception:
        pass
    if not pkg_ver:
        pkg_ver = full

    url = (f"https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/"
           f"libnvvm-{ver_dash}-{pkg_ver}-1.x86_64.rpm")
    tmp = "/tmp/amm_libnvvm.rpm"
    _log(task, f"下载 {url}")
    try:
        await _run(["curl", "-fsSL", "--http1.1", "--retry", "5", "--retry-all-errors",
                    "-C", "-", "-o", tmp, url], task, "download libnvvm")
        await _run(["rpm", "-ivh", "--nodeps", tmp], task, "install libnvvm")
    except Exception as e:
        raise RuntimeError(
            f"自动补装 libnvvm 失败（{e}）。请手动执行：\n"
            f"  curl -fsSL -o /tmp/libnvvm.rpm {url} && rpm -ivh --nodeps /tmp/libnvvm.rpm\n"
            "或改用 cpu flavor 安装 llama.cpp。") from e

    if not os.path.isfile(cicc):
        raise RuntimeError(f"安装后仍找不到 {cicc}，请检查 CUDA 安装是否完整")
    _log(task, "CUDA 工具链已补齐（nvvm/cicc）")


async def _build_llama(task: EngineTask, ver: str):
    d = _task_dir("llama_cpp", ver, task.flavor)
    src = d / "src"
    if not src.is_dir():
        await _download_llama(task, ver)
    use_cuda = task.flavor != "cpu"
    if use_cuda:
        await _ensure_cuda_toolchain(task)
    build = d / f"build-{task.flavor}"
    build.mkdir(parents=True, exist_ok=True)
    _log(task, f"cmake 配置 ({'CUDA ON' if use_cuda else 'CUDA OFF'})", 0.35)
    cfg = ["cmake", "-S", str(src), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"]
    # llama.cpp 新版（v1.0.0 起）会给 llama-server 内嵌一个 Web UI：
    # 构建时从 HF Bucket 拉 dist.tar.gz，校验脚本强制要求 workbox[hash].js，
    # 而发布的压缩包并不带这个文件 → llama-ui-assets 目标必失败，整个 server 编不出来。
    # AMM 自己有前端，不需要这个 UI，直接关掉（连同 npm 构建一起）。
    cfg += [
        "-DLLAMA_BUILD_UI=OFF",
        "-DLLAMA_USE_PREBUILT_UI=OFF",
    ]
    if use_cuda:
        nvcc = _cuda_nvcc()
        cfg += ["-DGGML_CUDA=ON"]
        if nvcc:
            cfg += [f"-DCMAKE_CUDA_COMPILER={nvcc}"]
        # native 在容器里偶尔挑不到正确架构，显式按 GPU 计算能力设置
        arch = _detect_cuda_arch()
        cfg += [f"-DCMAKE_CUDA_ARCHITECTURES={arch}" if arch else "-DCMAKE_CUDA_ARCHITECTURES=native"]
    else:
        cfg += ["-DGGML_CUDA=OFF"]
    await _run(cfg, task, "cmake")

    _log(task, "编译 llama-server + llama-tts（CPU 密集，可能 5-20 分钟）", 0.45)
    # llama-tts 是 TTS 必需：Qwen3-TTS 走 tools/tts 而不是 llama-server
    for target in ("llama-server", "llama-tts"):
        try:
            await _run(["cmake", "--build", str(build), "--config", "Release",
                        "-j", str(os.cpu_count() or 4), "--target", target], task, f"build:{target}")
        except Exception as e:
            if target == "llama-tts":
                _log(task, f"⚠️ llama-tts 编译失败（TTS 将不可用）：{e}")
            else:
                raise

    out_bin = build / "bin"
    dest = d / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    if not out_bin.is_dir():
        raise RuntimeError(f"编译产物缺失: {out_bin}")
    copied = 0
    for f in out_bin.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
            if f.name.startswith("llama-server"):
                os.chmod(dest / f.name, 0o755)
            copied += 1
    if not (dest / "llama-server").is_file():
        raise RuntimeError("llama-server 未生成")
    _log(task, f"安装完成：{dest / 'llama-server'}（{copied} 个文件）", 0.98)


async def _ensure_py_compatible(task: EngineTask, pkg: str, ver: str):
    """返回 (可用版本, 是否发生降级)。

    优先用传入版本；若其 requires_python 不接受当前解释器，
    则在该包已发布版本里挑一个兼容且尽量新的。
    """
    try:
        # 必须实时查（force），否则会拿到没有 py_ok 字段的旧缓存
        up = await upstream_versions(task.engine_type, refresh=True)
    except Exception:
        return ver, False

    versions = up.get("versions") or []
    if versions and "py_ok" not in versions[0]:
        # 兜底：缓存结构过旧，重新取一次
        cache = _read_json(_CACHE_PATH)
        cache.pop(task.engine_type, None)
        _write_json(_CACHE_PATH, cache)
        up = await upstream_versions(task.engine_type, refresh=True)
        versions = up.get("versions") or []

    rp = next((v.get("requires_python") for v in versions
               if v.get("version") == ver), None)
    _log(task, f"Python 兼容性检查：{pkg} {ver} requires_python={rp} "
               f"当前 {sys.version.split()[0]}")

    hit = next((v for v in versions if v.get("version") == ver), None)
    if hit is None:
        _log(task, f"⚠️ 上游清单里没有 {ver}，按请求继续")
        return ver, False
    if hit.get("py_ok", True):
        return ver, False

    cands = [v for v in versions if v.get("py_ok", True)]
    if not cands:
        raise RuntimeError(
            f"{pkg} {ver} 需要 Python {hit.get('requires_python')}，"
            f"当前是 {sys.version.split()[0]}，且没有兼容版本可用")
    # 按版本号而非发布时间排序，取最高
    best = max(cands, key=lambda v: _version_key(v["version"]))
    _log(task, f"{pkg} {ver} 需要 Python {hit.get('requires_python')}，"
               f"当前 {sys.version.split()[0]} → 改用 {best['version']}")
    return best["version"], True


async def _install_pip(task: EngineTask, pkg: str, ver: str):
    d = _task_dir(task.engine_type, ver, task.flavor)
    venv_path = d / "venv"
    d.mkdir(parents=True, exist_ok=True)
    py_bin = venv_path / "bin" / "python"
    if not py_bin.is_file():
        # 用当前 AMM 进程的解释器建 venv：容器里 python3 可能是 3.9，
        # 而服务本身跑在 3.11（/usr/bin/python3.11），混用会让
        # requires_python 判断与实际安装的包版本对不上。
        base_py = sys.executable or "python3"
        _log(task, f"创建虚拟环境（{base_py}）", 0.1)
        proc = await asyncio.create_subprocess_exec(
            base_py, "-m", "venv", str(venv_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("venv 创建失败")
    pip = str(venv_path / "bin" / "pip")

    torch_index = TORCH_INDEX.get(task.flavor, TORCH_INDEX["cuda"])
    if task.engine_type in ("diffusers", "sentence_transformers"):
        _log(task, f"安装 PyTorch ({task.flavor})", 0.2)
        await _run([pip, "install", "--upgrade", "pip"], task, "pip upgrade")
        torch_pkgs = ["torch", "torchvision"] if task.engine_type == "diffusers" else ["torch"]
        if task.flavor == "cuda":
            # 1) CUDA 运行时（精简镜像里没有 cuBLAS/cuDNN，torch cu128 需要）
            _log(task, "安装 CUDA 运行时（cublas / cudnn / nccl …）", 0.24)
            await _run([pip, "install", *TORCH_CUDA_RUNTIME_PKGS,
                        "-i", get_pip_index()], task, "install cuda runtime", env=_pip_env())
            # 2) torch 本体。官方 cu128 索引在大文件上常被切断，优先走 PyPI 镜像
            #    （阿里云的 torch 2.8 wheel 自带 cu128 二进制）。
            _log(task, "安装 torch (cu128)", 0.3)
            try:
                await _run([pip, "install", *torch_pkgs, "-i", get_pip_index()],
                           task, "install torch", env=_pip_env())
            except Exception as e:
                _log(task, f"PyPI 镜像装 torch 失败，回退官方 cu128 索引：{e}")
                await _run([pip, "install", *torch_pkgs, "--index-url", torch_index],
                           task, "install torch", env=_pip_env())
        else:
            await _run([pip, "install", *torch_pkgs, "--index-url", torch_index],
                       task, "install torch", env=_pip_env())

    # Python 版本预检：diffusers 0.37+ 要求 >=3.10，容器 python3.9 装不了。
    # 不兼容时自动降级到该解释器可用的最高版本，避免用户看到一堆
    # "Ignored the following versions that require a different python version"。
    ver, downgraded = await _ensure_py_compatible(task, pkg, ver)
    if downgraded:
        _log(task, f"⚠️ {ver} 与当前 Python 不兼容，已自动降级到 {ver}", 0.5)

    _log(task, f"安装 {pkg}=={ver}", 0.55)
    extra = []
    if task.engine_type == "diffusers":
        extra = ["transformers>=4.49.0", "accelerate>=1.0.0", "safetensors>=0.4.0",
                 "peft>=0.12.0", "modelscope>=1.21.0", "open_clip_torch",
                 "imageio", "imageio-ffmpeg", "gguf>=0.10.0"]
    elif task.engine_type == "sentence_transformers":
        extra = ["transformers>=4.46.0", "huggingface-hub>=0.26.0"]
    elif task.engine_type == "vllm":
        extra = ["huggingface-hub>=0.26.0"]
    await _run([pip, "install", f"{pkg}=={ver}", *extra, "-i", get_pip_index()],
               task, "pip install", env=_pip_env())

    bin_dir = d / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cli = venv_path / "bin" / ("vllm" if task.engine_type == "vllm" else "python")
    link = bin_dir / (cli.name)
    if cli.is_file():
        if link.exists() or link.is_symlink():
            try:
                link.unlink()
            except Exception:
                pass
        try:
            link.symlink_to(cli)
        except Exception:
            pass
    _log(task, f"安装完成: {venv_path}", 0.98)


def _pip_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_DEFAULT_TIMEOUT", "60")
    return env


async def _apply(task: EngineTask):
    meta = ENGINE_META[task.engine_type]
    if task.action == "uninstall":
        d = _task_dir(task.engine_type, task.version, task.flavor)
        _log(task, f"删除目录 {d}", 0.4)
        if d.exists():
            await asyncio.get_running_loop().run_in_executor(None, shutil.rmtree, str(d), True)
        _log(task, "已卸载", 1.0)
        return

    if task.action == "download":
        if meta["kind"] == "source-build":
            await _download_llama(task, task.version)
        else:
            await _download_pip(task, meta["pkg"], task.version)
        _log(task, "下载完成（尚未安装）", 1.0)
        return

    # install
    if meta["kind"] == "source-build":
        await _build_llama(task, task.version)
    else:
        await _install_pip(task, meta["pkg"], task.version)


async def _worker(task: EngineTask):
    task.status = "running"
    try:
        await _apply(task)
        task.status = "done"
        task.progress = 1.0
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)[:800]
        task.finished_at = time.time()
        _log(task, f"失败: {e}")
        logger.error(f"[engine-task {task.task_id}] {e}")
    finally:
        _RUNNING.pop(task.task_id, None)
        try:
            _persist_tasks()
        except Exception:
            pass


def _persist_tasks():
    _write_json(_TASKS_PATH, [t.to_dict(tail=200) for t in list(_TASKS.values())[-40:]])


def _load_tasks():
    for row in (_read_json(_TASKS_PATH) or []):
        if not isinstance(row, dict) or not row.get("task_id"):
            continue
        if row.get("status") in ("pending", "running"):
            row["status"] = "failed"
            row["error"] = row.get("error") or "服务重启，任务中断"
        _TASKS[row["task_id"]] = EngineTask(
            task_id=row["task_id"], engine_type=row.get("engine_type", ""),
            version=row.get("version", ""), flavor=row.get("flavor", ""),
            action=row.get("action", ""), status=row.get("status", "failed"),
            progress=row.get("progress", 0), message=row.get("message", ""),
            log=row.get("log") or [], error=row.get("error", ""),
            started_at=row.get("started_at") or time.time(),
            finished_at=row.get("finished_at"),
        )


try:
    _load_tasks()
except Exception as _e:  # pragma: no cover
    logger.warning(f"加载引擎任务历史失败: {_e}")


def start_task(engine_type: str, version: str, flavor: str, action: str) -> Dict[str, Any]:
    meta = ENGINE_META.get(engine_type)
    if not meta:
        raise ValueError(f"未知引擎: {engine_type}")
    if not version or not _SAFE_VER.match(version):
        raise ValueError(f"版本号非法: {version}")
    flavors = meta["flavors"]
    if flavor and flavor not in flavors:
        raise ValueError(f"{engine_type} 不支持 flavor={flavor}（可选: {', '.join(flavors)}）")
    if not flavor:
        flavor = meta["default_flavor"]
    if action not in ("download", "install", "uninstall"):
        raise ValueError(f"未知操作: {action}")

    active = find_active(engine_type, version, flavor, action)
    if active:
        return {"ok": True, "task": active, "reused": True}

    tid = f"{action}-{engine_type}-{version}-{flavor}-{int(time.time())}"
    task = EngineTask(task_id=tid, engine_type=engine_type, version=version, flavor=flavor, action=action)
    _TASKS[tid] = task
    loop = asyncio.get_event_loop()
    _RUNNING[tid] = loop.create_task(_worker(task))
    return {"ok": True, "task": task.to_dict()}
