#!/usr/bin/env python3
"""
AMM - 模型自动下载 API (v0.4)
===============================
支持 ModelScope 与 HuggingFace 两种源的模型下载，
提供下载任务注册、进度查询。

约定:
- 下载到 `MODELS_DIR/zoo/modelscope`（ModelScope）或 `MODELS_DIR/zoo/huggingface`（HF）
- 用独立子进程执行，避免阻塞 aiohttp 事件循环
- 任务状态: pending | downloading | done | failed
"""
import os
import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import yaml
from aiohttp import web

logger = logging.getLogger("AMM.Download")

# 下载根目录
_MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
CACHE_MAP = {
    "modelscope": "/models/zoo/modelscope",
    "huggingface": "/models/zoo/huggingface",
}


class DownloadTask:
    def __init__(self, task_id: str, model_id: str, source: str, category: str = ""):
        self.task_id = task_id
        self.model_id = model_id
        self.source = source
        self.category = category
        self.status = "pending"          # pending | downloading | done | failed
        self.created = time.time()
        self.started = None
        self.finished = None
        self.error = ""
        self.detail = ""
        self.proc = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "source": self.source,
            "category": self.category,
            "status": self.status,
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

    # ------------------------------------------------------------
    # 下载执行
    # ------------------------------------------------------------
    async def start(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)

        model_id = (body.get("model_id") or "").strip()
        source = (body.get("source") or "modelscope").strip().lower()
        category = (body.get("category") or "").strip()
        if not model_id or model_id.startswith("__"):
            return self._json({"error": "缺少有效 model_id"}, 400)
        if source not in ("modelscope", "huggingface"):
            return self._json({"error": f"source 仅支持 modelscope/huggingface: {source}"}, 400)

        task_id = f"dl_{int(time.time())}_{len(self.tasks)}"
        task = DownloadTask(task_id, model_id, source, category)
        with self._lock:
            self.tasks[task_id] = task

        # 后台启动下载
        threading.Thread(target=self._run_download, args=(task,), daemon=True).start()
        return self._json({"ok": True, "task_id": task_id, **task.to_dict()})

    def _run_download(self, task: DownloadTask):
        try:
            task.status = "downloading"
            task.started = time.time()
            # 使用 vLLM venv 的 python（已装 modelscope / huggingface_hub）
            py = self._resolve_python()
            cache = CACHE_MAP.get(task.source, CACHE_MAP["modelscope"])
            os.makedirs(cache, exist_ok=True)
            script = _DOWNLOAD_SCRIPT.format(
                source=task.source, model_id=task.model_id, cache=cache
            )
            task.proc = subprocess.Popen(
                [py, "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=self._env(), cwd="/amm",
            )
            task.detail = "下载中..."
            out, _ = task.proc.communicate(timeout=None)
            task.finished = time.time()
            if task.proc.returncode == 0:
                task.status = "done"
                task.detail = (out or "").strip()[-500:]
                # 若指定了类别，尝试刷新模型配置
                if task.category:
                    try:
                        self.manager._load_config()
                    except Exception:
                        pass
            else:
                task.status = "failed"
                task.error = (out or "").strip()[-500:]
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.finished = time.time()

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------
    async def status(self, req) -> web.Response:
        task_id = req.query.get("task_id", "")
        if task_id:
            t = self.tasks.get(task_id)
            if not t:
                return self._json({"error": "任务不存在"}, 404)
            return self._json(t.to_dict())
        tasks = [t.to_dict() for t in self.tasks.values()]
        tasks.sort(key=lambda x: x["created"], reverse=True)
        return self._json({"tasks": tasks[:20], "count": len(tasks)})

    # ------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------
    def _resolve_python(self) -> str:
        for p in [
            "/amm/backend/engines_installed/vllm/0.22.1/venv/bin/python",
            "/usr/bin/python3.11",
        ]:
            if os.path.isfile(p):
                return p
        return "python3"

    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["MODELSCOPE_CACHE"] = CACHE_MAP["modelscope"]
        env["HF_HOME"] = "/models/huggingface"
        return env

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                 headers={"Access-Control-Allow-Origin": "*"})


_DOWNLOAD_SCRIPT = """
import os, sys
source = "{source}"
model_id = "{model_id}"
cache = "{cache}"
if source == "modelscope":
    os.environ.setdefault("MODELSCOPE_CACHE", cache)
    os.environ.setdefault("MODELSCOPE_DOMAIN", "modelscope.cn")
    try:
        from modelscope import snapshot_download
    except Exception as e:
        print("MODELSCOPE_NOT_AVAILABLE", e); sys.exit(3)
    p = snapshot_download(model_id, cache_dir=cache)
elif source == "huggingface":
    os.environ.setdefault("HF_HOME", "/models/huggingface")
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        print("HF_NOT_AVAILABLE", e); sys.exit(3)
    p = snapshot_download(repo_id=model_id, cache_dir=cache)
else:
    print("BAD_SOURCE"); sys.exit(2)
print("OK", p)
"""


def setup_routes(app: web.Application, manager):
    bridge = DownloadBridge(manager)
    app.router.add_post("/api/models/download", bridge.start)
    app.router.add_get("/api/models/download/status", bridge.status)