#!/usr/bin/env python3
"""
AMM - AI Models Manage Backend Server
======================================
混合推理引擎架构: vllm / llama.cpp / diffusers
9 类 AI 模型统一管理与推理调度平台
"""
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml
import aiohttp
from aiohttp import web

# 确保 /amm 在 sys.path，使 'backend' 包可被发现（无论从哪启动）
import sys as _sys
_AMM_PROJECT = os.environ.get("AMM_ROOT", "/amm")
for _p in (_AMM_PROJECT, str(Path(__file__).resolve().parent.parent)):
    if _p and _p not in _sys.path:
        _sys.path.insert(0, _p)

from backend.core.model_manager import ModelManager, AMM_ROOT, LOGS_DIR
from backend.api.openai_bridge import setup_routes as setup_openai_routes
from backend.api.diffusers_bridge import setup_routes as setup_diffusers_routes
from backend.api.fs_bridge import setup_routes as setup_fs_routes

# ============================================================
# Paths
# ============================================================
BASE_DIR = Path(AMM_ROOT) if Path(AMM_ROOT).exists() else Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "backend" / "config" / "models_config.yaml"
os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "amm_server.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("AMM")

# ============================================================
# API Handlers
# ============================================================
class APIHandlers:
    def __init__(self, manager: ModelManager):
        self.manager = manager

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                  headers={"Access-Control-Allow-Origin": "*"})

    # ---- System ----
    async def get_system_info(self, req):
        return self._json(self.manager.get_system_info())

    # ---- Health ----
    async def health_check(self, req):
        return self._json({"status": "ok", "timestamp": datetime.now().isoformat()})

    # ---- Settings ----
    async def get_settings(self, req):
        sc = self.manager.config.get("server", {})
        return self._json({
            "host": sc.get("host", "0.0.0.0"),
            "port": sc.get("port", 80),
            "models_dir": sc.get("models_dir", "/models"),
            "logs_dir": sc.get("logs_dir", "/amm/logs"),
            "version": "v0.2",
        })

    # ---- Models Config ----
    async def get_models_config(self, req):
        cfg = self.manager.get_config()
        return self._json({k: v for k, v in cfg.items() if k != "server"})

    async def get_model_config_detail(self, req):
        model_id = req.match_info["model_id"]
        for key in self.manager._model_keys():
            cfg = self.manager.config.get(key, {})
            if cfg.get("id") == model_id:
                return self._json({
                    "models": cfg.get("available_models", []),
                    "parameters": cfg.get("parameters", []),
                    "available_engines": cfg.get("available_engines", []),
                    "port": cfg.get("port", 0),
                    "id": model_id,
                })
        return self._json({"error": "模型类别未找到"}, 404)

    # ---- Instances ----
    async def get_all_instances(self, req):
        self.manager.refresh_instances()
        result = {}
        for inst in self.manager.get_all_instances().values():
            result[inst.model_id] = inst.to_dict()
        return self._json(result)

    async def get_instance(self, req):
        model_id = req.match_info["model_id"]
        self.manager.refresh_instances()
        inst = self.manager.get_instance(model_id)
        if not inst:
            return self._json({"error": "模型未找到"}, 404)
        return self._json(inst.to_dict())

    # ---- Start/Stop/Restart ----
    async def start_model(self, req):
        model_id = req.match_info["model_id"]
        result = await self.manager.start_model(model_id)
        return self._json(result)

    async def stop_model(self, req):
        model_id = req.match_info["model_id"]
        result = await self.manager.stop_model(model_id)
        return self._json(result)

    async def restart_model(self, req):
        model_id = req.match_info["model_id"]
        result = await self.manager.restart_model(model_id)
        return self._json(result)

    # ---- Parameters ----
    async def update_parameters(self, req):
        model_id = req.match_info["model_id"]
        try:
            data = await req.json()
            self.manager.update_parameters(model_id, data)
            return self._json({"success": True})
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def update_advanced_settings(self, req):
        """更新 Diffusers 引擎的高级设置 (FP8 量化 / CPU offload / boundary 等), 写 yaml (2026-08-05)"""
        model_id = req.match_info["model_id"]
        try:
            data = await req.json()
            self.manager.update_advanced_settings(model_id, data)
            return self._json({"success": True, "settings": data})
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def get_advanced_settings(self, req):
        """读取 Diffusers 高级设置, 直接从 yaml 取 (source of truth)"""
        model_id = req.match_info["model_id"]
        try:
            settings = self.manager.get_advanced_settings(model_id)
            return self._json({"success": True, "model_id": model_id, "settings": settings})
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def update_model_file(self, req):
        model_id = req.match_info["model_id"]
        try:
            data = await req.json()
            self.manager.update_model_file(model_id, data.get("model_file", ""))
            return self._json({"success": True})
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    # ---- Engine ----
    async def select_engine(self, req):
        """为模型选择推理引擎"""
        model_id = req.match_info["model_id"]
        try:
            data = await req.json()
            engine_type = data.get("engine_type", "")
            engine_version = data.get("engine_version", "")
            self.manager.update_engine(model_id, engine_type, engine_version)
            return self._json({"success": True, "engine_type": engine_type, "engine_version": engine_version})
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def get_engines_info(self, req):
        """获取所有引擎信息"""
        return self._json(self.manager.get_engine_info())

    async def get_engine_versions(self, req):
        """获取所有引擎版本"""
        result = await self.manager.get_engine_versions()
        return self._json(result)

    async def install_engine(self, req):
        """安装引擎版本"""
        try:
            data = await req.json()
            engine_type = data.get("engine_type", "")
            version = data.get("version", "")
            result = await self.manager.install_engine_version(engine_type, version)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def uninstall_engine(self, req):
        """卸载引擎版本"""
        try:
            data = await req.json()
            engine_type = data.get("engine_type", "")
            version = data.get("version", "")
            result = await self.manager.uninstall_engine_version(engine_type, version)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    # ---- Logs ----
    async def get_model_logs(self, req):
        model_id = req.match_info["model_id"]
        lines = int(req.query.get("lines", 100))
        logs = await self.manager.get_model_logs(model_id, lines)
        return self._json({"logs": logs})

    async def get_server_logs(self, req):
        lines = int(req.query.get("lines", 100))
        logs = await self.manager.get_server_logs(lines)
        return self._json({"logs": logs})

    # ---- GPU ----
    async def get_gpu_info(self, req):
        return self._json({"gpus": self.manager.get_gpu_info()})

    # ---- Frontend ----
    async def serve_index(self, req):
        p = BASE_DIR / "frontend" / "index.html"
        return web.FileResponse(p) if p.exists() else self._json({"error": "index.html not found"}, 404)


# ============================================================
# Application
# ============================================================
def create_app() -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    manager = ModelManager(str(CONFIG_PATH))
    h = APIHandlers(manager)

    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
            return resp
        return await handler(request)

    app.middlewares.append(cors_middleware)

    # Static
    frontend_dir = BASE_DIR / "frontend"
    if frontend_dir.exists():
        app.router.add_static('/css/', path=str(frontend_dir / "css"))
        app.router.add_static('/js/', path=str(frontend_dir / "js"))
        app.router.add_static('/assets/', path=str(frontend_dir / "assets"))

    # Routes (non-param first to avoid conflicts)
    app.router.add_get("/", h.serve_index)
    app.router.add_get("/api/health", h.health_check)
    app.router.add_get("/api/system", h.get_system_info)
    app.router.add_get("/api/settings", h.get_settings)
    app.router.add_get("/api/gpu", h.get_gpu_info)
    app.router.add_get("/api/config/models", h.get_models_config)
    app.router.add_get("/api/instances", h.get_all_instances)
    app.router.add_get("/api/logs/server", h.get_server_logs)

    # Engine management
    app.router.add_get("/api/engines", h.get_engines_info)
    app.router.add_get("/api/engines/versions", h.get_engine_versions)
    app.router.add_post("/api/engines/install", h.install_engine)
    app.router.add_post("/api/engines/uninstall", h.uninstall_engine)

    # Per-model routes
    app.router.add_get("/api/config/models/{model_id}", h.get_model_config_detail)
    app.router.add_get("/api/instances/{model_id}", h.get_instance)
    app.router.add_post("/api/instances/{model_id}/start", h.start_model)
    app.router.add_post("/api/instances/{model_id}/stop", h.stop_model)
    app.router.add_post("/api/instances/{model_id}/restart", h.restart_model)
    app.router.add_put("/api/instances/{model_id}/parameters", h.update_parameters)
    app.router.add_put("/api/instances/{model_id}/advanced", h.update_advanced_settings)
    app.router.add_get("/api/instances/{model_id}/advanced", h.get_advanced_settings)
    app.router.add_put("/api/instances/{model_id}/model-file", h.update_model_file)
    app.router.add_put("/api/instances/{model_id}/engine", h.select_engine)
    app.router.add_get("/api/instances/{model_id}/logs", h.get_model_logs)

    # OpenAI / Diffusers bridges
    setup_openai_routes(app, manager)
    setup_diffusers_routes(app, manager)
    setup_fs_routes(app, manager)

    # Monitor
    async def on_startup(app):
        app['monitor_task'] = asyncio.create_task(monitor_loop(manager))

    async def on_cleanup(app):
        if 'monitor_task' in app:
            app['monitor_task'].cancel()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app['manager_state'] = manager
    return app


async def monitor_loop(manager):
    while True:
        try:
            manager.refresh_instances()
        except Exception as e:
            logger.error(f"监控循环异常: {e}")
        await asyncio.sleep(5)


# ============================================================
# Main
# ============================================================
async def main():
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        sys.exit(1)

    host = config.get("server", {}).get("host", "0.0.0.0")
    # 统一监听端口 (容器内 80, 宿主机映射 60006):
    #   同时承载 WebUI 页面(/、/api管理、/static) 与 OpenAI 兼容 API(/v1/*)
    #   前端与外部客户端均通过 http://host:60006 访问 (相对路径同源)
    port = config.get("server", {}).get("port", 80)

    app = create_app()
    logger.info(f"AMM 启动: http://{host}:{port}  (WebUI + OpenAI 兼容 API, 宿主机映射 60006)")

    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"[AMM] 已监听 0.0.0.0:{port} (宿主机映射 60006): WebUI + OpenAI API /v1/* + 管理 /api/*")

    # 监控循环由 app.on_startup 启动一次即可
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
