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
_PROXY_CONF = "/amm/backend/config/download_proxy.json"
_PROGRESS_DIR = "/models/.download-progress"


def _load_proxy() -> dict:
    try:
        with open(_PROXY_CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "url": ""}


class DownloadTask:
    def __init__(self, task_id: str, model_id: str, source: str, category: str = "",
                 revision: str = "", files: Optional[list] = None,
                 target_parent: str = "", target_folder: str = ""):
        self.task_id = task_id
        self.model_id = model_id
        self.source = source
        self.category = category
        self.revision = revision
        self.files = files or []
        self.target_parent = target_parent
        self.target_folder = target_folder
        self.proxy = _load_proxy()
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
            "target_parent": self.target_parent,
            "target_folder": self.target_folder,
            "proxy": self.proxy,
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
        py = self._resolve_python()
        env = self._build_env({"AMM_OP": "revisions", "AMM_SOURCE": source,
                               "AMM_MODEL": model_id,
                               "AMM_PROXY": json.dumps(_load_proxy())})
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
        # 目标落盘位置(可空, 空则保持默认 zoo 快照结构)
        target_parent = (body.get("target_parent") or "").strip().strip("/")
        target_folder = (body.get("target_folder") or "").strip().lstrip("/")
        if target_folder:
            # 校验: 不含危险字符/路径穿越
            if any(ch in target_folder for ch in ("..", "/", "\\", "\0")):
                return self._json({"error": "文件夹名称含非法字符"}, 400)
        if not model_id or model_id.startswith("__"):
            return self._json({"error": "缺少有效 model_id"}, 400)
        if source not in ("modelscope", "huggingface"):
            return self._json({"error": f"source 仅支持 modelscope/huggingface: {source}"}, 400)

        task_id = f"dl_{int(time.time())}_{len(self.tasks)}"
        task = DownloadTask(task_id, model_id, source, category, revision, files,
                            target_parent, target_folder)
        task.total = total
        task.total_files = total_files
        with self._lock:
            self.tasks[task_id] = task
        threading.Thread(target=self._run_download, args=(task,), daemon=True).start()
        return self._json({"ok": True, "task_id": task_id, **task.to_dict()})

    def _run_download(self, task: DownloadTask):
        try:
            task.status = "downloading"
            task.started = time.time()
            py = self._resolve_python()
            cache = CACHE_MAP.get(task.source, CACHE_MAP["modelscope"])
            # 仅在未指定目标文件夹时才预建默认 cache; 否则等 relocate 直接在目标目录落盘
            if not task.target_folder:
                os.makedirs(cache, exist_ok=True)
            os.makedirs(_PROGRESS_DIR, exist_ok=True)
            task.progress_file = os.path.join(_PROGRESS_DIR, f"{task.task_id}.json")
            if os.path.exists(task.progress_file):
                try:
                    os.remove(task.progress_file)
                except Exception:
                    pass
            env = self._build_env({
                "AMM_OP": "download", "AMM_SOURCE": task.source,
                "AMM_MODEL": task.model_id, "AMM_CACHE": cache,
                "AMM_REVISION": task.revision, "AMM_FILES": json.dumps(task.files),
                "AMM_PROXY": json.dumps(task.proxy), "AMM_PROGRESS": task.progress_file,
                "AMM_TOTAL": str(task.total),
                "AMM_TARGET_PARENT": task.target_parent,
                "AMM_TARGET_FOLDER": task.target_folder,
                "AMM_MODELS_DIR": os.environ.get("MODELS_DIR", "/models"),
            })
            task.proc = subprocess.Popen([py, "-c", self._helper_source()],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, env=env, cwd="/amm")
            task.detail = "下载中..."

            def _poll():
                last_growth = time.time()
                last_dl = 0
                while True:
                    if task.proc.poll() is not None:
                        break
                    self._read_progress(task)
                    # 停滞检测: downloaded 在 90s 内无增长则终止(比 speed 更稳, 不受抖动影响)
                    if task.downloaded > last_dl:
                        last_dl = task.downloaded
                        last_growth = time.time()
                    if task.downloaded > 0 and (time.time() - last_growth) > 180:
                        logger.warning(f"Download {task.task_id} no progress in 180s, terminating")
                        try:
                            task.proc.terminate()
                        except Exception:
                            pass
                        task.status = "failed"
                        task.error = "下载无进展超过 3 分钟, 已终止。请检查代理/网络后重试(支持断点续传)"
                        break
                    time.sleep(2)
            tp = threading.Thread(target=_poll, daemon=True)
            tp.start()
            out, _ = task.proc.communicate(timeout=None)
            tp.join(timeout=5)
            self._read_progress(task)
            task.finished = time.time()
            rc = task.proc.returncode
            task.proc = None
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
        for p in [
            "/amm/backend/engines_installed/vllm/0.22.1/venv/bin/python",
            "/usr/bin/python3.11",
        ]:
            if os.path.isfile(p):
                return p
        return "python3"

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


def setup_routes(app: web.Application, manager):
    bridge = DownloadBridge(manager)
    app.router.add_get("/api/models/download/proxy", bridge.get_proxy)
    app.router.add_post("/api/models/download/proxy", bridge.set_proxy)
    app.router.add_post("/api/models/downloads", bridge.revisions)
    app.router.add_post("/api/models/download", bridge.start)
    app.router.add_get("/api/models/download/status", bridge.status)
    app.router.add_post("/api/models/download/cancel", bridge.cancel)