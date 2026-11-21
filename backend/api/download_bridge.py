#!/usr/bin/env python3
"""
AMM - 模型自动下载 API (v0.5)
===============================
支持 ModelScope 与 HuggingFace 两种源的模型下载。

特性:
- HTTP 代理设置（全局，可开关，持久化）。
- 模型版本(revision)/分支查询 + 每版本文件清单 + 文件大小预览。
- 下载支持断点续传、下载速度显示、剩余时间预测。
- 可选: 只下载某 revision / 指定文件子集。

约定:
- 下载到 `MODELS_DIR/zoo/modelscope`（ModelScope）或 `MODELS_DIR/zoo/huggingface`（HF）。
- 用独立子进程执行, 避免阻塞 aiohttp 事件循环。
- 进度: 子进程内线程每秒统计 cache 目录占用并写入临时 JSON, 父进程读取。
"""
import asyncio
import os
import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from aiohttp import web

logger = logging.getLogger("AMM.Download")

CACHE_MAP = {
    "modelscope": "/models/zoo/modelscope",
    "huggingface": "/models/zoo/huggingface",
}
_AMM_ROOT = os.environ.get("AMM_ROOT", "/amm")
_PROXY_CONF = os.path.join(_AMM_ROOT, "backend", "config", "download_proxy.json")
_SETTINGS_CONF = os.path.join(_AMM_ROOT, "backend", "config", "download_settings.json")
_PROGRESS_DIR = "/models/.download-progress"

# 下载专用 venv：主进程/镜像不一定装 huggingface_hub、modelscope，
# 首次下载时自动创建并安装，避免「引擎没装就不能下载」的死锁。
_DOWNLOAD_VENV = os.path.join(
    os.environ.get("AMM_DATA_DIR", os.path.join(_AMM_ROOT, "data")), "venvs", "download", "venv")
_VENV_LOCK = threading.Lock()
_PIP_INDEX = "https://mirrors.aliyun.com/pypi/simple/"

# HuggingFace 对同一出口 IP 的并发大文件下载很敏感：
# 实测 5 个任务同时跑会集体断连（peer closed connection）。
# 这里用全局串行队列，一次只下一个，剩下的排队。
_DOWNLOAD_SLOTS = threading.Semaphore(int(os.environ.get("AMM_DOWNLOAD_CONCURRENCY", "1")))
_DOWNLOAD_RETRIES = int(os.environ.get("AMM_DOWNLOAD_RETRIES", "4"))

# HuggingFace 站点候选（前端下拉可直接选）
# 默认走 hf-mirror.com：它在 308 之后由 CDN 供数，配合 HF_HUB_DISABLE_XET=1
# 实测比直连官方稳定（官方直连在本机频繁 429 / 断流）。
HF_ENDPOINT_PRESETS = [
    {"url": "https://hf-mirror.com", "label": "hf-mirror.com（国内镜像，推荐）"},
    {"url": "https://huggingface.co", "label": "huggingface.co（官方，可能限流）"},
]


def _load_proxy() -> dict:
    try:
        with open(_PROXY_CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "url": ""}


def _load_settings() -> dict:
    """下载设置：HuggingFace 镜像站 + API Token（token 不落明文回显）"""
    data = {"hf_endpoint": "https://hf-mirror.com", "hf_token": "", "pypi_index": ""}
    try:
        with open(_SETTINGS_CONF, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update({k: v for k, v in saved.items() if v is not None})
    except Exception:
        pass
    if not data.get("hf_endpoint"):
        data["hf_endpoint"] = "https://hf-mirror.com"
    return data


def _save_settings(data: dict) -> dict:
    os.makedirs(os.path.dirname(_SETTINGS_CONF), exist_ok=True)
    with open(_SETTINGS_CONF, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def _mask_token(tok: str) -> str:
    if not tok:
        return ""
    if len(tok) <= 8:
        return tok[:2] + "*" * (len(tok) - 2)
    return f"{tok[:4]}…{tok[-4:]}"


class DownloadTask:
    def __init__(self, task_id: str, model_id: str, source: str, category: str = "",
                 revision: str = "", files: Optional[list] = None,
                 target_dir: str = ""):
        self.task_id = task_id
        self.model_id = model_id
        self.source = source
        self.category = category
        self.revision = revision
        self.files = files or []
        # 目标落盘目录(相对 MODELS_DIR 的路径, 可含子目录, 空则保留默认 zoo 快照)
        self.target_dir = target_dir
        self.proxy = _load_proxy()
        self.settings = _load_settings()
        self.status = "pending"
        self.created = time.time()
        self.started = None
        self.finished = None
        self.error = ""
        self.detail = ""
        self.proc = None
        self.progress_file = ""
        self.downloaded = 0
        self.total = 0
        self.speed = 0.0
        self.eta = None
        self.downloaded_files = 0
        self.total_files = 0
        self.current_file = ""

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "source": self.source,
            "category": self.category,
            "revision": self.revision,
            "files": self.files,
            "target_dir": self.target_dir,
            "proxy": self.proxy,
            "hf_endpoint": self.settings.get("hf_endpoint", ""),
            "status": self.status,
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": round(self.speed, 1),
            "eta": self.eta,
            "downloaded_files": self.downloaded_files,
            "total_files": self.total_files,
            "current_file": self.current_file,
            "created_at": self.created,
            "started_at": self.started,
            "finished_at": self.finished,
            "error": self.error,
            "detail": self.detail,
        }


class DownloadBridge:
    def __init__(self, manager):
        self.manager = manager
        self.tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()

    # ---------------- 代理设置 ----------------
    async def get_proxy(self, req) -> web.Response:
        return self._json({"proxy": _load_proxy()})

    async def set_proxy(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        enabled = bool(body.get("enabled"))
        url = (body.get("url") or "").strip()
        if enabled and not url:
            return self._json({"error": "开启代理但未提供代理地址"}, 400)
        data = {"enabled": enabled, "url": url}
        try:
            os.makedirs(os.path.dirname(_PROXY_CONF), exist_ok=True)
            with open(_PROXY_CONF, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return self._json({"error": f"代理设置保存失败: {e}"}, 500)
        return self._json({"ok": True, "proxy": data})

    # ---------------- 下载设置（HF 镜像 / Token） ----------------
    async def get_settings(self, req) -> web.Response:
        s = _load_settings()
        return self._json({
            "settings": {
                "hf_endpoint": s.get("hf_endpoint", ""),
                "hf_token_masked": _mask_token(s.get("hf_token", "")),
                "has_hf_token": bool(s.get("hf_token")),
            },
            "presets": HF_ENDPOINT_PRESETS,
        })

    async def set_settings(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        cur = _load_settings()
        if "hf_endpoint" in body:
            ep = (body.get("hf_endpoint") or "").strip().rstrip("/")
            if ep and not ep.startswith(("http://", "https://")):
                return self._json({"error": "镜像地址需以 http(s):// 开头"}, 400)
            cur["hf_endpoint"] = ep or "https://huggingface.co"
        if "hf_token" in body:
            tok = (body.get("hf_token") or "").strip()
            # 传空字符串 = 清除；传 "********" = 不改
            if tok and set(tok) == {"*"}:
                pass
            else:
                cur["hf_token"] = tok
        _save_settings(cur)
        return self._json({
            "ok": True,
            "settings": {
                "hf_endpoint": cur.get("hf_endpoint", ""),
                "hf_token_masked": _mask_token(cur.get("hf_token", "")),
                "has_hf_token": bool(cur.get("hf_token")),
            },
        })

    # ---------------- 版本/文件清单查询 ----------------
    async def revisions(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        model_id = (body.get("model_id") or "").strip()
        source = (body.get("source") or "huggingface").strip().lower()
        if not model_id:
            return self._json({"error": "缺少 model_id"}, 400)
        if source not in ("huggingface", "modelscope"):
            return self._json({"error": f"source 仅支持 huggingface/modelscope: {source}"}, 400)
        try:
            py = await asyncio.get_running_loop().run_in_executor(None, self.ensure_download_env)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 500)
        settings = _load_settings()
        env = self._build_env({"AMM_OP": "revisions", "AMM_SOURCE": source,
                               "AMM_MODEL": model_id,
                               "AMM_PROXY": json.dumps(_load_proxy()),
                               "AMM_HF_ENDPOINT": settings.get("hf_endpoint", ""),
                               "AMM_HF_TOKEN": settings.get("hf_token", "")})
        try:
            r = subprocess.run([py, "-c", self._helper_source()], capture_output=True,
                               text=True, timeout=240, env=env, cwd="/amm")
        except subprocess.TimeoutExpired:
            return self._json({"error": "版本查询超时(网络不可达或模型过大)"}, 504)
        if r.returncode != 0:
            return self._json({"error": (r.stderr or r.stdout or "").strip()[-800:]}, 502)
        try:
            return self._json(json.loads((r.stdout or "").strip()))
        except Exception as e:
            return self._json({"error": f"结果解析失败: {e}; 输出:{(r.stdout or '')[-300:]}"}, 502)

    # ---------------- 下载执行 ----------------
    async def start(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        model_id = (body.get("model_id") or "").strip()
        source = (body.get("source") or "modelscope").strip().lower()
        category = (body.get("category") or "").strip()
        revision = (body.get("revision") or "").strip()
        files = body.get("files") or []
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        total = int(body.get("total") or 0)
        total_files = int(body.get("total_files") or 0)
        # 目标落盘目录(相对 /models, 可含多级且可含文件名; 空则保留默认 zoo 快照)
        # 兼容旧参数 target_parent/target_folder
        target_dir = (body.get("target_dir") or "").strip().strip("/")
        old_parent = (body.get("target_parent") or "").strip().strip("/")
        old_folder = (body.get("target_folder") or "").strip().lstrip("/")
        if not target_dir and (old_parent or old_folder):
            target_dir = (old_parent + "/" if old_parent else "") + old_folder
        if target_dir:
            # 校验: 不允许含 .. / 空字符 (允许斜杠作多级路径)
            if ".." in target_dir.split("/") or "\0" in target_dir:
                return self._json({"error": "目标路径含非法字符"}, 400)
        if not model_id or model_id.startswith("__"):
            return self._json({"error": "缺少有效 model_id"}, 400)
        if source not in ("modelscope", "huggingface"):
            return self._json({"error": f"source 仅支持 modelscope/huggingface: {source}"}, 400)

        task_id = f"dl_{int(time.time())}_{len(self.tasks)}"
        task = DownloadTask(task_id, model_id, source, category, revision, files, target_dir)
        task.total = total
        task.total_files = total_files
        with self._lock:
            self.tasks[task_id] = task
        threading.Thread(target=self._run_download, args=(task,), daemon=True).start()
        return self._json({"ok": True, "task_id": task_id, **task.to_dict()})

    def _run_download(self, task: DownloadTask):
        """排队后执行；同一时刻只跑 AMM_DOWNLOAD_CONCURRENCY 个（默认 1）。"""
        with _DOWNLOAD_SLOTS:
            if task.status == "cancelled":
                return
            self._run_download_inner(task)

    def _run_download_inner(self, task: DownloadTask):
        try:
            task.status = "downloading"
            task.started = time.time()
            task.detail = "准备下载环境…"
            py = self.ensure_download_env()
            cache = CACHE_MAP.get(task.source, CACHE_MAP["modelscope"])
            # 若未指定目标目录, 预建默认 zoo 快照; 否则下载后由 relocate 落到目标目录
            if not task.target_dir:
                os.makedirs(cache, exist_ok=True)
            os.makedirs(_PROGRESS_DIR, exist_ok=True)
            task.progress_file = os.path.join(_PROGRESS_DIR, f"{task.task_id}.json")
            if os.path.exists(task.progress_file):
                try:
                    os.remove(task.progress_file)
                except Exception:
                    pass
            # HuggingFace 源：有 target_dir 且指定了文件时走原生 HTTP Range 通道，
            # 绕开 huggingface_hub 在镜像上 400-500MB 处必断的传输层。
            op = "download"
            if (task.source == "huggingface" and task.target_dir
                    and task.files and not _use_hub_download()):
                op = "download_http"

            env = self._build_env({
                "AMM_OP": op, "AMM_SOURCE": task.source,
                "AMM_MODEL": task.model_id, "AMM_CACHE": cache,
                "AMM_REVISION": task.revision, "AMM_FILES": json.dumps(task.files),
                "AMM_PROXY": json.dumps(task.proxy), "AMM_PROGRESS": task.progress_file,
                "AMM_TOTAL": str(task.total),
                "AMM_TARGET_DIR": task.target_dir,
                "AMM_MODELS_DIR": os.environ.get("MODELS_DIR", "/models"),
                "AMM_HF_ENDPOINT": (task.settings or {}).get("hf_endpoint", ""),
                "AMM_HF_TOKEN": (task.settings or {}).get("hf_token", ""),
            })
            task.detail = "下载中..."

            def _poll():
                last_growth = time.time()
                last_dl = 0
                while True:
                    proc = task.proc
                    if proc is None or proc.poll() is not None:
                        break
                    self._read_progress(task)
                    # 停滞检测: downloaded 在 90s 内无增长则终止(比 speed 更稳, 不受抖动影响)
                    if task.downloaded > last_dl:
                        last_dl = task.downloaded
                        last_growth = time.time()
                    if task.downloaded > 0 and (time.time() - last_growth) > 180:
                        logger.warning(f"Download {task.task_id} no progress in 180s, terminating")
                        try:
                            if task.proc is not None:
                                task.proc.terminate()
                        except Exception:
                            pass
                        task.status = "failed"
                        task.error = "下载无进展超过 3 分钟, 已终止。请检查代理/网络后重试(支持断点续传)"
                        break
                    time.sleep(2)
            # 断点续传 + 重试：HF 大文件偶发 "peer closed connection"，
            # huggingface_hub 会复用已下载分片，重跑即可续上。
            rc = 1
            out = ""
            for attempt in range(1, _DOWNLOAD_RETRIES + 1):
                if task.status == "cancelled":
                    break
                if attempt > 1:
                    wait = min(30, 5 * attempt)
                    task.detail = f"连接中断，{wait}s 后第 {attempt}/{_DOWNLOAD_RETRIES} 次重试（断点续传）…"
                    logger.warning(f"Download {task.task_id} retry {attempt}/{_DOWNLOAD_RETRIES} in {wait}s")
                    time.sleep(wait)
                    task.status = "downloading"

                task.proc = subprocess.Popen([py, "-c", self._helper_source()],
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, env=env, cwd="/amm")
                tp = threading.Thread(target=_poll, daemon=True)
                tp.start()
                out, _ = task.proc.communicate(timeout=None)
                tp.join(timeout=5)
                self._read_progress(task)
                rc = task.proc.returncode
                task.proc = None
                if task.status == "cancelled":
                    break
                if rc == 0:
                    break

            task.finished = time.time()
            if task.status == "cancelled":
                task.error = "任务已取消"
                return
            if rc == 0:
                task.status = "done"
                raw = (out or "").strip()
                # 解析辅助脚本的落盘结果行: `OK <path>` 或 `OK`
                if raw.startswith("OK"):
                    parts = raw.split(None, 1)
                    task.detail = ("下载完成，已落盘: " + parts[1]) if len(parts) > 1 else "下载完成"
                else:
                    task.detail = raw[-400:] or "下载完成"
                if task.category:
                    try:
                        self.manager._load_config()
                    except Exception:
                        pass
            else:
                task.status = "failed"
                task.error = (out or "").strip()[-600:]
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.finished = time.time()

    def _read_progress(self, task: DownloadTask):
        try:
            if not task.progress_file or not os.path.exists(task.progress_file):
                return
            with open(task.progress_file, encoding="utf-8") as f:
                p = json.load(f)
            task.downloaded = int(p.get("downloaded", task.downloaded))
            task.total = int(p.get("total", task.total)) or task.total
            task.speed = float(p.get("speed", 0))
            task.eta = p.get("eta")
            task.downloaded_files = int(p.get("downloaded_files", task.downloaded_files))
            task.total_files = int(p.get("total_files", task.total_files)) or task.total_files
            task.current_file = p.get("current_file", task.current_file) or ""
            task.detail = p.get("detail", task.detail)
        except Exception:
            pass

    # ---------------- 查询 ----------------
    async def status(self, req) -> web.Response:
        task_id = req.query.get("task_id", "")
        if task_id:
            t = self.tasks.get(task_id)
            if not t:
                return self._json({"error": "任务不存在"}, 404)
            self._read_progress(t)
            return self._json(t.to_dict())
        tasks = []
        for t in self.tasks.values():
            if t.status in ("downloading", "pending"):
                self._read_progress(t)
            tasks.append(t.to_dict())
        tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return self._json({"tasks": tasks[:20], "count": len(tasks)})

    async def cancel(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        task_id = (body.get("task_id") or "").strip()
        t = self.tasks.get(task_id)
        if not t:
            return self._json({"error": "任务不存在"}, 404)
        if t.proc and t.proc.poll() is None:
            t.proc.terminate()
        t.status = "cancelled"
        return self._json({"ok": True, "task_id": task_id})

    # ---------------- helpers ----------------
    def _resolve_python(self) -> str:
        """挑一个装了 huggingface_hub / modelscope 的解释器。

        v1.1 起引擎根目录可配置（AMM_ENGINES_ROOT，容器里是 /opt/amm-engines），
        不能再写死 /amm/backend/engines_installed/... 。这里按优先级找：
          1. 各引擎已安装的 venv（vLLM/Diffusers/ST 都会带 huggingface_hub）
          2. 系统 python3 / python3.11
        """
        import glob

        roots = [
            os.environ.get("AMM_ENGINES_ROOT", ""),
            os.path.join(os.environ.get("AMM_ROOT", "/amm"), "backend", "engines_installed"),
        ]
        candidates: list = []
        # 自举的下载 venv 优先
        candidates.append(os.path.join(_DOWNLOAD_VENV, "bin", "python"))
        for root in roots:
            if not root:
                continue
            for pattern in ("*/[0-9a-zA-Z._+-]*/venv/bin/python", "*/venv/bin/python"):
                candidates += sorted(glob.glob(os.path.join(root, pattern)), reverse=True)
        candidates += [
            "/usr/bin/python3.11",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            "python3",
        ]

        # 优先挑真带 huggingface_hub 或 modelscope 的解释器
        for p in candidates:
            if not (p == "python3" or os.path.isfile(p)):
                continue
            try:
                probe = subprocess.run(
                    [p, "-c", "import importlib.util as u;"
                              "print(int(bool(u.find_spec('huggingface_hub') or u.find_spec('modelscope'))))"],
                    capture_output=True, text=True, timeout=15,
                )
                if probe.returncode == 0 and probe.stdout.strip() == "1":
                    return p
            except Exception:
                continue

        # 都不满足时退回第一个存在的解释器，让错误信息里能看到真实 import 失败
        for p in candidates:
            if p == "python3" or os.path.isfile(p):
                return p
        return "python3"

    def ensure_download_env(self) -> str:
        """确保下载专用 venv 可用（缺 huggingface_hub / modelscope 时自动装）。

        返回该 venv 的 python 路径；创建失败时抛 RuntimeError 带真实 pip 输出。
        """
        py = os.path.join(_DOWNLOAD_VENV, "bin", "python")
        if os.path.isfile(py):
            probe = subprocess.run(
                [py, "-c", "import huggingface_hub, modelscope"],
                capture_output=True, text=True,
            )
            if probe.returncode == 0:
                return py

        with _VENV_LOCK:
            # 双检：可能已被并发调用创建好
            if os.path.isfile(py):
                probe = subprocess.run(
                    [py, "-c", "import huggingface_hub, modelscope"],
                    capture_output=True, text=True,
                )
                if probe.returncode == 0:
                    return py

            logger.info(f"初始化下载专用 venv: {_DOWNLOAD_VENV}")
            os.makedirs(os.path.dirname(_DOWNLOAD_VENV), exist_ok=True)
            base = next((p for p in ("/usr/local/bin/python3", "/usr/bin/python3.11",
                                     "/usr/bin/python3", "python3")
                         if p == "python3" or os.path.isfile(p)), "python3")
            mk = subprocess.run([base, "-m", "venv", _DOWNLOAD_VENV],
                                capture_output=True, text=True)
            if mk.returncode != 0:
                raise RuntimeError(f"创建下载 venv 失败: {(mk.stderr or mk.stdout)[-500:]}")

            pip = os.path.join(_DOWNLOAD_VENV, "bin", "pip")
            for args in (["install", "--upgrade", "pip", "-i", _PIP_INDEX],
                         ["install", "huggingface_hub[hf_transfer]>=0.26.0",
                          "modelscope>=1.21.0", "hf-transfer",
                          "-i", _PIP_INDEX]):
                r = subprocess.run([pip, *args], capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    raise RuntimeError(
                        f"下载依赖安装失败 ({args[1]}): {(r.stderr or r.stdout)[-600:]}")
        return py

    def _helper_source(self) -> str:
        # 从同目录外部文件读取, 便于维护; 缺失时用内嵌兜底
        helper = Path(__file__).parent / "_download_helper.py"
        if helper.exists():
            try:
                return helper.read_text(encoding="utf-8")
            except Exception:
                pass
        raise RuntimeError("缺少下载辅助脚本 _download_helper.py")

    def _build_env(self, extra: Dict[str, str] = None) -> Dict[str, str]:
        env = dict(os.environ)
        env["MODELSCOPE_CACHE"] = CACHE_MAP["modelscope"]
        env["HF_HOME"] = "/models/huggingface"
        if extra:
            env.update(extra)
        return env

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                 headers={"Access-Control-Allow-Origin": "*"})


def _use_hub_download() -> bool:
    """是否强制走 huggingface_hub（默认走 HTTP Range 通道）。

    AMM_USE_HUB_DOWNLOAD=1 可切回 hub 实现（例如需要私有仓库鉴权/gated 模型）。
    """
    return os.environ.get("AMM_USE_HUB_DOWNLOAD", "0").strip().lower() in ("1", "true", "yes")


def setup_routes(app: web.Application, manager):
    bridge = DownloadBridge(manager)
    app.router.add_get("/api/models/download/proxy", bridge.get_proxy)
    app.router.add_post("/api/models/download/proxy", bridge.set_proxy)
    app.router.add_get("/api/models/download/settings", bridge.get_settings)
    app.router.add_post("/api/models/download/settings", bridge.set_settings)
    app.router.add_post("/api/models/downloads", bridge.revisions)
    app.router.add_post("/api/models/download", bridge.start)
    app.router.add_get("/api/models/download/status", bridge.status)
    app.router.add_post("/api/models/download/cancel", bridge.cancel)