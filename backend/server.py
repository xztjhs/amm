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
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

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
from backend.api.file_manager_bridge import setup_routes as setup_fm_routes
from backend.api.download_bridge import setup_routes as setup_download_routes
from backend.api.logs_bridge import setup_routes as setup_logs_routes
from backend.api.hotswap_bridge import setup_routes as setup_hotswap_routes
from backend.api.templates_bridge import setup_routes as setup_templates_routes
from backend.api.router_bridge import setup_routes as setup_router_routes
from backend.api.openai_ext import setup_routes as setup_openai_ext_routes
from backend.api.engine_bridge import setup_routes as setup_engine_routes
from backend.engines.quantizer import setup_routes as setup_quantize_routes

# ============================================================
# Paths
# ============================================================
BASE_DIR = Path(AMM_ROOT) if Path(AMM_ROOT).exists() else Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "backend" / "config" / "models_config.yaml"
os.makedirs(LOGS_DIR, exist_ok=True)

# ============================================================
# 版本常量（唯一版本来源；升级时只需改这里）
#   页脚 / Settings / /api/settings 均从此读取
# ============================================================
AMM_VERSION = os.environ.get("AMM_VERSION", "v1.9.4")
AMM_DOCS_DIR = BASE_DIR / "docs" / "manual"

# ============================================================
# 日志：带轮转，避免无限增长
#   实测 0.5s 轮询下 amm_server.log 增长 ~30MB/小时，
#   之前单文件跑到 174MB。默认 50MB × 5 备份。
# ============================================================
def _build_log_handlers():
    """日志落地策略。

    容器里 entrypoint 用 `nohup python3.11 server.py >> amm_server.out` 启动，
    stdout 直接追加到一个**不会轮转**的文件。原先 StreamHandler 挂在 root 上
    且是 INFO 级，于是 aiohttp 的访问日志（0.5s 轮询 × 3 个接口）把
    amm_server.out 刷到 240MB+（≈150MB/天）。

    现在的分工：
      - amm_server.log   RotatingFileHandler，INFO 全量（排障主入口，会轮转）
      - amm_server.out   StreamHandler，默认 WARNING 以上（异常/崩溃回溯，量极小）
      - access.log       访问日志独立 RotatingFileHandler，不再进 .out
    """
    from logging.handlers import RotatingFileHandler

    max_bytes = int(os.environ.get("AMM_LOG_MAX_BYTES", str(50 * 1024 * 1024)))
    backups = int(os.environ.get("AMM_LOG_BACKUPS", "5"))
    level_name = os.environ.get("AMM_STDOUT_LOG_LEVEL", "WARNING").strip().upper()
    stdout_level = getattr(logging, level_name, None)
    if not isinstance(stdout_level, int):
        stdout_level = logging.WARNING

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(stdout_level)
    handlers = [stream]
    try:
        handlers.append(RotatingFileHandler(
            os.path.join(LOGS_DIR, "amm_server.log"),
            maxBytes=max_bytes, backupCount=backups, encoding="utf-8"))
    except Exception:
        # 轮转配置异常时退回普通 FileHandler，不能让服务起不来
        handlers.append(logging.FileHandler(
            os.path.join(LOGS_DIR, "amm_server.log"), encoding="utf-8"))
    return handlers


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=_build_log_handlers()
)


def _setup_access_log():
    """访问日志单独落到会轮转的 access.log，别再污染 stdout/.out。

    前端默认 0.5s 轮询 /api/instances、/api/system、/api/bridge/diffusers/status，
    一个开着 WebUI 的标签页就是 6 请求/秒 —— 全量记录会写掉 ~130MB/天。
    这里用 filter 把这些「健康轮询」请求静音（可用 AMM_ACCESS_LOG_POLL=1 打开），
    其余接口（/v1/* 推理、POST/PUT/DELETE 变更操作）照常记录。
    """
    acc = logging.getLogger("aiohttp.access")
    if os.environ.get("AMM_ACCESS_LOG", "1").strip().lower() in ("0", "false", "no", "off"):
        acc.disabled = True
        return
    # 切断向 root 传播，避免同一条访问日志又被写进 amm_server.log 和 .out
    acc.propagate = False
    if acc.handlers:
        return
    try:
        from logging.handlers import RotatingFileHandler
        h = RotatingFileHandler(
            os.path.join(LOGS_DIR, "access.log"),
            maxBytes=int(os.environ.get("AMM_ACCESS_LOG_MAX_BYTES", str(20 * 1024 * 1024))),
            backupCount=int(os.environ.get("AMM_ACCESS_LOG_BACKUPS", "2")),
            encoding="utf-8")
        h.setFormatter(logging.Formatter(
            '%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        if os.environ.get("AMM_ACCESS_LOG_POLL", "0").strip().lower() not in (
                "1", "true", "yes", "on"):
            h.addFilter(_PollNoiseFilter())
        acc.addHandler(h)
        acc.setLevel(logging.INFO)
    except Exception:
        # 建不出独立文件时宁可丢掉访问日志，也不让它再刷 .out
        acc.disabled = True


class _PollNoiseFilter(logging.Filter):
    """丢弃前端健康轮询产生的 GET 记录，只留真实调用。

    注意 aiohttp 的实际实现：AccessLogger.log 里是
        self.logger.info(self._log_format % tuple(values), extra=extra)
    也就是说消息**已经格式化好**写进 record.msg，record.args 是空的。
    所以只能解析 msg 字符串（形如 `1.2.3.4 [...] "GET /api/system HTTP/1.1" 200 1292`）。
    若将来 aiohttp 改成传 args，这里两条路都覆盖。
    """

    # 这些路径是前端定时器在刷，没有诊断价值
    _POLL_PATHS = (
        "/api/system",
        "/api/instances",
        "/api/bridge/diffusers/status",
        "/api/health",
    )
    # 从 "GET /path HTTP/1.1" 里抠出 method 与 path
    _REQ_RE = re.compile(r'"([A-Z]+)\s+(\S+)\s+HTTP/[\d.]+"?')

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            method, path = self._extract(record)
            if not method:
                return True          # 认不出来就放行，宁可多记也不误删
            if method != "GET":
                return True          # 变更操作一律记录
            if path.startswith("/v1/"):
                return True          # 推理调用一律记录
            return not any(path == p or path.startswith(p + "?") or path.startswith(p + "/")
                           for p in self._POLL_PATHS)
        except Exception:
            return True

    def _extract(self, record: logging.LogRecord):
        """优先用 args 里的 request 对象，兜底解析 record.msg 字符串。"""
        try:
            args = getattr(record, "args", None)
            if args:
                req = args[0]
                method = getattr(req, "method", "") or ""
                path = getattr(req, "path", "") or ""
                if method and path:
                    return method, path
        except Exception:
            pass
        try:
            msg = record.getMessage()
        except Exception:
            return "", ""
        m = self._REQ_RE.search(msg)
        if not m:
            return "", ""
        return m.group(1), m.group(2)


_setup_access_log()

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
            "version": AMM_VERSION,
            # 对客户端暴露的「组织/分组」名（/v1/models 的 owned_by 用它替代 UUID）
            "node_name": sc.get("node_name", "") or "AMM",
            # 品牌（#10）：项目名 / 副标题 / LOGO 路径，前端用它同步 title 与顶栏
            "app_name": sc.get("app_name", "") or "AMM",
            "app_subtitle": sc.get("app_subtitle", "") or "AI Models Manage",
            "app_logo": sc.get("app_logo", "/assets/logo.png"),
        })

    async def update_brand(self, req):
        """更新项目名称 / 副标题 / LOGO 路径（#10）

        只接受指定字段，其余配置原样保留；写回 yaml。
        """
        try:
            body = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "请求体必须是对象"}, 400)

        sc = self.manager.config.setdefault("server", {})
        changed = {}
        for key, limit in (("app_name", 40), ("app_subtitle", 60), ("app_logo", 200)):
            if key not in body:
                continue
            val = str(body.get(key) or "").strip()
            if len(val) > limit:
                return self._json({"error": f"{key} 过长（≤{limit} 字符）"}, 400)
            if key == "app_name" and not val:
                return self._json({"error": "项目名称不能为空"}, 400)
            sc[key] = val
            changed[key] = val

        if not changed:
            return self._json({"error": "没有可更新的字段"}, 400)
        try:
            import yaml as _yaml
            with open(self.manager.config_path, "w", encoding="utf-8") as f:
                _yaml.safe_dump(self.manager.config, f, allow_unicode=True,
                                sort_keys=False, default_flow_style=False)
        except Exception as e:
            logger.warning(f"保存品牌配置失败: {e}")
            return self._json({"error": f"写入配置失败: {e}"}, 500)
        logger.info(f"品牌配置已更新: {changed}")
        return self._json({"success": True, "changed": changed, **self._brand_view()})

    def _brand_view(self) -> Dict[str, Any]:
        sc = self.manager.config.get("server", {})
        return {
            "app_name": sc.get("app_name", "") or "AMM",
            "app_subtitle": sc.get("app_subtitle", "") or "AI Models Manage",
            "app_logo": sc.get("app_logo", "/assets/logo.png"),
        }

    async def update_server_config(self, req):
        """修改服务目录类配置（#9）

        只允许改「路径类」字段，不碰 host/port（改了会把服务启动配置弄坏）。
        host/port/models_dir/logs_dir 的修改在下次重启后生效。
        """
        try:
            body = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "请求体必须是对象"}, 400)

        ALLOWED = {"models_dir", "logs_dir", "data_dir", "templates_dir", "verification_dir"}
        sc = self.manager.config.setdefault("server", {})
        changed = {}
        for key in ALLOWED & set(body):
            val = str(body.get(key) or "").strip()
            if not val:
                continue
            if not val.startswith("/"):
                return self._json({"error": f"{key} 必须是绝对路径"}, 400)
            if not os.path.isdir(val):
                return self._json({"error": f"{key} 目录不存在: {val}"}, 400)
            sc[key] = val
            changed[key] = val

        if not changed:
            return self._json({"error": "没有可更新的字段（允许: "
                                        + ", ".join(sorted(ALLOWED)) + "）"}, 400)
        try:
            import yaml as _yaml
            with open(self.manager.config_path, "w", encoding="utf-8") as f:
                _yaml.safe_dump(self.manager.config, f, allow_unicode=True,
                                sort_keys=False, default_flow_style=False)
        except Exception as e:
            return self._json({"error": f"写入配置失败: {e}"}, 500)
        logger.info(f"服务目录配置已更新: {changed}（重启后生效）")
        return self._json({"success": True, "changed": changed,
                           "needs_restart": True})

    async def get_paths(self, req):
        """目录类配置与它们的实际可用性（#9）"""
        sc = self.manager.config.get("server", {})
        items = []
        for key, default in (("models_dir", "/models"),
                             ("logs_dir", "/amm/logs"),
                             ("data_dir", "/amm/data"),
                             ("templates_dir", "/amm/templates"),
                             ("verification_dir", "/amm/verification")):
            val = sc.get(key) or default
            items.append({
                "key": key, "path": val,
                "exists": os.path.isdir(val),
                "writable": os.access(val, os.W_OK),
            })
        return self._json({"paths": items})

    async def update_node_name(self, req):
        """更新对客户端展示的节点/分组名"""
        try:
            body = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        name = str((body or {}).get("node_name") or "").strip()
        if not name:
            return self._json({"error": "node_name 不能为空"}, 400)
        if len(name) > 64:
            return self._json({"error": "node_name 过长（≤64 字符）"}, 400)
        sc = self.manager.config.setdefault("server", {})
        sc["node_name"] = name
        try:
            import yaml as _yaml
            with open(self.manager.config_path, "w", encoding="utf-8") as f:
                _yaml.safe_dump(self.manager.config, f, allow_unicode=True,
                                sort_keys=False, default_flow_style=False)
        except Exception as e:
            logger.warning(f"保存 node_name 失败: {e}")
            return self._json({"error": f"写入配置失败: {e}"}, 500)
        return self._json({"success": True, "node_name": name})

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

    # ---- v1.0: 动态实例 (running-model 模式) ----
    async def launch_instance(self, req):
        """POST /api/instances/launch  创建并启动动态模型实例"""
        try:
            data = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        if not isinstance(data, dict):
            return self._json({"error": "请求体必须是对象"}, 400)
        result = await self.manager.launch_instance(data)
        status = 400 if "error" in result else 200
        return self._json(result, status)

    async def terminate_instance(self, req):
        """DELETE /api/instances/{model_id}  注销动态模型实例"""
        model_id = req.match_info["model_id"]
        result = await self.manager.terminate_instance(model_id)
        status = 400 if "error" in result else 200
        return self._json(result, status)

    async def get_running_models(self, req):
        """GET /api/running-models  运行中模型列表"""
        return self._json({"models": self.manager.get_running_models()})

    async def get_instance(self, req):
        model_id = req.match_info["model_id"]
        self.manager.refresh_instances()
        inst = self.manager.get_instance(model_id)
        if not inst:
            return self._json({"error": "模型未找到"}, 404)
        return self._json(inst.to_dict())

    # ---- Start/Stop/Restart ----
    async def start_model(self, req):
        """启动模型。可选 body: {version, flavor} 指定引擎版本与 CPU/GPU 构建。"""
        model_id = req.match_info["model_id"]
        try:
            body = await req.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            inst = self.manager.instances.get(model_id)
            if inst is not None:
                if body.get("version"):
                    inst.engine_version = str(body["version"]).strip()
                if body.get("flavor"):
                    fl = str(body["flavor"]).strip().lower()
                    if fl in ("cpu", "cuda"):
                        inst.flavor = fl
                if body.get("version") or body.get("flavor"):
                    try:
                        self.manager._save_state()
                    except Exception:
                        pass
        result = await self.manager.start_model(model_id)
        return self._json(result)

    async def engine_variants(self, req):
        """GET /api/instances/{model_id}/variants

        返回该实例可选引擎的所有「版本 + flavor」组合及是否已安装，
        供 Dashboard 启动时选择 CPU / CUDA 构建。
        """
        model_id = req.match_info["model_id"]
        inst = self.manager.instances.get(model_id)
        if not inst:
            return self._json({"error": f"实例不存在: {model_id}"}, 404)

        from pathlib import Path
        import glob as _glob
        root = Path(os.environ.get("AMM_ENGINES_ROOT",
                                   os.path.join(os.environ.get("AMM_ROOT", "/amm"),
                                                "backend", "engines_installed")))
        eng_root = root / inst.engine_type

        variants = []
        if eng_root.is_dir():
            for d in sorted(eng_root.iterdir()):
                if not d.is_dir():
                    continue
                name = d.name
                # 形如 b12381-cuda / 0.39.0-cuda
                if "-" in name:
                    ver, flavor = name.rsplit("-", 1)
                    if flavor not in ("cpu", "cuda"):
                        ver, flavor = name, ""
                else:
                    ver, flavor = name, ""
                # 判断是否真的装好了
                installed = False
                if inst.engine_type == "llama_cpp":
                    installed = (d / "bin" / "llama-server").is_file()
                else:
                    installed = (d / "venv" / "bin" / "python").is_file()
                if flavor in ("cpu", "cuda") or installed:
                    variants.append({
                        "version": ver,
                        "flavor": flavor,
                        "installed": installed,
                        "label": f"{ver} · {flavor}" if flavor else ver,
                    })

        return self._json({
            "model_id": model_id,
            "engine_type": inst.engine_type,
            "current": {"version": inst.engine_version or "", "flavor": inst.flavor or ""},
            "variants": variants,
        })

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

    async def switch_engine(self, req):
        """切换实例使用的推理引擎（Dashboard Model Status 用）。

        body: {engine_type, restart}
        切引擎后原有参数可能不适用，这里保留用户显式设置的参数，
        由引擎侧按新引擎过滤无关项。
        """
        model_id = req.match_info["model_id"]
        try:
            body = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        engine = str((body or {}).get("engine_type") or "").strip()
        if not engine:
            return self._json({"error": "缺少 engine_type"}, 400)
        inst = self.manager.instances.get(model_id)
        if not inst:
            return self._json({"error": f"实例不存在: {model_id}"}, 404)
        eng_obj = self.manager.registry.get(engine)
        if eng_obj is None:
            return self._json({
                "error": f"未知引擎: {engine}",
                "available": list(self.manager.registry.list_all().keys()),
            }, 400)
        supported = eng_obj.get_supported_categories()
        if inst.category not in supported:
            return self._json({
                "error": f"引擎 {engine} 不支持类别 {inst.category}",
                "supported": supported,
            }, 400)

        was_running = inst.status == "running"
        inst.engine_type = engine
        try:
            self.manager._save_state()
        except Exception:
            pass
        if not (body or {}).get("restart", True) or not was_running:
            return self._json({"success": True, "engine_type": engine, "restarted": False})

        try:
            await self.manager.stop_model(model_id)
        except Exception as e:
            logger.warning(f"切换引擎前停止实例失败 {model_id}: {e}")
        try:
            r = await self.manager.start_model(model_id)
        except Exception as e:
            return self._json({"success": False, "engine_type": engine,
                               "error": f"引擎已切换，但启动失败: {e}"}, 500)
        if isinstance(r, dict) and r.get("error"):
            return self._json({"success": False, "engine_type": engine,
                               "error": r["error"]}, 500)
        return self._json({"success": True, "engine_type": engine, "restarted": True})

    async def apply_parameters(self, req):
        """保存参数并重启实例，让改动真正生效（Running Models 用）。

        body: {parameters: {...}, restart: true}
        返回保存结果 + 重启结果；重启失败时仍保留已保存的参数。
        """
        model_id = req.match_info["model_id"]
        try:
            body = await req.json()
        except Exception as e:
            return self._json({"error": f"invalid json: {e}"}, 400)
        params = body.get("parameters") if isinstance(body, dict) else None
        if not isinstance(params, dict):
            return self._json({"error": "parameters 必须是对象"}, 400)
        inst = self.manager.instances.get(model_id)
        if not inst:
            return self._json({"error": f"实例不存在: {model_id}"}, 404)
        try:
            self.manager.update_parameters(model_id, params)
        except Exception as e:
            return self._json({"error": f"保存参数失败: {e}"}, 400)
        if not body.get("restart", True):
            return self._json({"success": True, "saved": True, "restarted": False})
        try:
            result = await self.manager.restart_model(model_id)
        except Exception as e:
            return self._json({"success": False, "saved": True,
                               "error": f"参数已保存，但重启失败: {e}"}, 500)
        if result.get("error"):
            return self._json({"success": False, "saved": True,
                               "restart": result, "error": result["error"]}, 500)
        return self._json({"success": True, "saved": True, "restarted": True,
                           "restart": result})

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
        """安装引擎版本 (支持 flavor: cpu | cuda)"""
        try:
            data = await req.json()
            engine_type = data.get("engine_type", "")
            version = data.get("version", "")
            flavor = data.get("flavor", "") or ""
            result = await self.manager.install_engine_version(engine_type, version, flavor)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def uninstall_engine(self, req):
        """卸载引擎版本 (支持 flavor)"""
        try:
            data = await req.json()
            engine_type = data.get("engine_type", "")
            version = data.get("version", "")
            flavor = data.get("flavor", "") or ""
            result = await self.manager.uninstall_engine_version(engine_type, version, flavor)
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

    # ---- Settings Actions (v0.2) ----
    async def reload_config(self, req):
        """重新加载 models_config.yaml (热更新配置) """
        try:
            self.manager._load_config()
            logger.info("配置已重新加载 (settings/reload)")
            return self._json({"ok": True, "message": "配置已重载"})
        except Exception as e:
            logger.error(f"重载配置失败: {e}")
            return self._json({"ok": False, "error": str(e)}, 500)

    async def restart_server(self, req):
        """重启 AMM 服务：先重载配置并刷新实例状态。
        注意：容器内 server 即 PID1，直接杀进程会终止容器；
        这里做安全重载(配置+实例刷新)。完整重启请使用 docker restart AMM。"""
        try:
            self.manager._load_config()
            self.manager.refresh_instances()
            return self._json({"ok": True, "message": "配置已重载并刷新实例; 完整重启请: docker restart AMM"})
        except Exception as e:
            logger.error(f"重启/重载失败: {e}")
            return self._json({"ok": False, "error": str(e)}, 500)

    # ---- Startup Command (v0.6) ----
    async def get_startup_command(self, req):
        model_id = req.match_info["model_id"]
        return self._json(self.manager.get_startup_command(model_id))

    async def build_command_preview(self, req):
        model_id = req.match_info["model_id"]
        result = await self.manager.build_command_preview(model_id)
        return self._json(result)

    async def save_startup_command(self, req):
        model_id = req.match_info["model_id"]
        try:
            data = await req.json()
            command = data.get("command", "")
            result = self.manager.save_startup_command(model_id, command)
            return self._json(result)
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    async def clear_startup_command(self, req):
        model_id = req.match_info["model_id"]
        return self._json(self.manager.clear_startup_command(model_id))

    # ---- GPU ----
    async def get_gpu_info(self, req):
        return self._json({"gpus": self.manager.get_gpu_info()})

    # ---- Frontend ----
    # 手册：返回可搜索的文档目录（markdown 列表 + 内容）
    async def get_docs_summary(self, req):
        """GET /api/docs"""
        docs = []
        if AMM_DOCS_DIR.exists():
            for f in sorted(AMM_DOCS_DIR.glob("*.md")):
                try:
                    title = f.stem
                    # 取首个 # 标题作为可读标题
                    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
                    docs.append({"file": f.name, "title": title})
                except Exception:
                    continue
        return self._json({"version": AMM_VERSION, "docs": docs})

    async def get_docs_content(self, req):
        """GET /api/docs/{filename}  返回指定手册的 markdown 内容"""
        name = req.match_info["filename"]
        # 防路径穿越
        if not name or ".." in name or "/" in name or "\\" in name:
            return self._json({"error": "invalid doc name"}, 400)
        p = AMM_DOCS_DIR / name
        if not p.exists():
            return self._json({"error": f"doc not found: {name}"}, 404)
        return web.Response(text=p.read_text(encoding="utf-8", errors="ignore"),
                            content_type="text/markdown", charset="utf-8")

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

    @web.middleware
    async def api_key_middleware(request, handler):
        """可选的外网访问门禁：Settings 里配置了默认 API Key 才生效。

        - 只拦 /v1/*（对外推理接口）
        - 不拦 /api/*：那是 Web 控制台自己的接口，拦了界面就打不开了
        - 放行：带正确 X-API-Key / Authorization: Bearer
        - 也放行本机回环（AMM 自己内部调用、健康检查）
        """
        path = request.path
        if not path.startswith("/v1/"):
            return await handler(request)
        router = app.get("token_router")
        if not router:
            return await handler(request)

        # 判定必须和 router.authenticate() 一致，否则会出现
        # 「没设默认 Key 但签过虚拟 Key → 仍然 401」这种自相矛盾（缺陷 DEV-03）。
        # 单一来源：只要一条路径会放行就放行。
        dk = ""
        enforced = False
        try:
            info = router.get_default_key(reveal=True)
            dk = info.get("key") or ""
            enforced = bool(info.get("enforced"))
        except Exception:
            dk, enforced = "", False

        # 是否启用鉴权只看 auth_enforced（唯一开关）以及是否存在默认 Key。
        # 不能因为「存着虚拟 Key」就强制拦截 —— disable_auth_entirely() 会保留
        # 虚拟 Key（为了历史/配额不丢），那条路径下必须真的放行（#2）。
        if not dk and not enforced:
            return await handler(request)

        # 本机回环放行（AMM 内部转发、探活）
        peer = request.transport.get_extra_info("peername") if request.transport else None
        if peer and peer[0] in ("127.0.0.1", "::1"):
            return await handler(request)

        got = request.headers.get("X-API-Key", "").strip()
        if not got:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                got = auth[7:].strip()
        # 虚拟 key / 默认 key 均可用于访问 /v1
        ok, _reason, _cfg = router.authenticate(got)
        if (dk and got == dk) or ok:
            return await handler(request)
        return web.json_response(
            {"error": {"message": "invalid-api-key", "type": "auth_error"}},
            status=401, headers={"Access-Control-Allow-Origin": "*"})

    app.middlewares.append(api_key_middleware)

    # Static
    frontend_dir = BASE_DIR / "frontend"
    if frontend_dir.exists():
        app.router.add_static('/css/', path=str(frontend_dir / "css"))
        app.router.add_static('/js/', path=str(frontend_dir / "js"))
        app.router.add_static('/assets/', path=str(frontend_dir / "assets"))

    # Routes (non-param first to avoid conflicts)
    app.router.add_get("/", h.serve_index)
    app.router.add_get("/api/docs", h.get_docs_summary)
    app.router.add_get("/api/docs/{filename}", h.get_docs_content)
    app.router.add_get("/api/health", h.health_check)
    app.router.add_get("/api/system", h.get_system_info)
    app.router.add_get("/api/settings", h.get_settings)
    app.router.add_post("/api/settings/node-name", h.update_node_name)
    app.router.add_post("/api/settings/brand", h.update_brand)
    app.router.add_get("/api/settings/paths", h.get_paths)
    app.router.add_post("/api/settings/paths", h.update_server_config)
    app.router.add_get("/api/gpu", h.get_gpu_info)
    app.router.add_get("/api/config/models", h.get_models_config)
    app.router.add_get("/api/instances", h.get_all_instances)
    app.router.add_get("/api/running-models", h.get_running_models)
    app.router.add_post("/api/instances/launch", h.launch_instance)
    app.router.add_get("/api/logs/server", h.get_server_logs)

    # Engine management
    app.router.add_get("/api/engines", h.get_engines_info)
    app.router.add_get("/api/engines/versions", h.get_engine_versions)
    app.router.add_post("/api/engines/install", h.install_engine)
    app.router.add_post("/api/engines/uninstall", h.uninstall_engine)
    # Settings actions (v0.2)
    app.router.add_post("/api/settings/reload", h.reload_config)
    app.router.add_post("/api/settings/restart", h.restart_server)

    # Per-model routes
    app.router.add_get("/api/config/models/{model_id}", h.get_model_config_detail)
    app.router.add_get("/api/instances/{model_id}", h.get_instance)
    app.router.add_delete("/api/instances/{model_id}", h.terminate_instance)
    app.router.add_post("/api/instances/{model_id}/start", h.start_model)
    app.router.add_post("/api/instances/{model_id}/stop", h.stop_model)
    app.router.add_post("/api/instances/{model_id}/restart", h.restart_model)
    app.router.add_get("/api/instances/{model_id}/variants", h.engine_variants)
    app.router.add_put("/api/instances/{model_id}/apply", h.apply_parameters)
    app.router.add_put("/api/instances/{model_id}/engine", h.switch_engine)
    app.router.add_put("/api/instances/{model_id}/parameters", h.update_parameters)
    app.router.add_put("/api/instances/{model_id}/advanced", h.update_advanced_settings)
    app.router.add_get("/api/instances/{model_id}/advanced", h.get_advanced_settings)
    app.router.add_put("/api/instances/{model_id}/model-file", h.update_model_file)
    app.router.add_put("/api/instances/{model_id}/engine", h.select_engine)
    app.router.add_get("/api/instances/{model_id}/logs", h.get_model_logs)
    app.router.add_get("/api/instances/{model_id}/command", h.get_startup_command)
    app.router.add_post("/api/instances/{model_id}/command/preview", h.build_command_preview)
    app.router.add_put("/api/instances/{model_id}/command", h.save_startup_command)
    app.router.add_delete("/api/instances/{model_id}/command", h.clear_startup_command)

    # OpenAI / Diffusers bridges
    # 注意顺序：必须先注册 Token Router（它创建 app["token_router"]），
    # 否则 /v1/models 汇总 provider 模型时拿不到 router，客户端看不到外部模型。
    setup_router_routes(app, manager)
    setup_openai_routes(app, manager)
    setup_diffusers_routes(app, manager)
    setup_fs_routes(app, manager)
    setup_fm_routes(app, manager)
    setup_download_routes(app, manager)
    setup_logs_routes(app, manager)
    setup_hotswap_routes(app, manager)
    setup_templates_routes(app, manager)
    setup_openai_ext_routes(app, manager, router=app.get("token_router"))
    setup_engine_routes(app, manager)
    setup_quantize_routes(app, manager)

    # Monitor
    async def on_startup(app):
        app['monitor_task'] = asyncio.create_task(monitor_loop(manager))
        # v1.0: 自动恢复之前处于运行状态的动态实例
        try:
            app['restore_task'] = asyncio.create_task(manager.restore_running_instances())
        except Exception as e:
            logger.warning(f"动态实例自动恢复启动失败: {e}")

        # 预热引擎目录体积缓存：冷启动时第一次扫描要 7-13s（D3），
        # 放到后台线程做，用户打开向导时直接命中缓存（~2ms）。
        async def _warm_engine_size_cache():
            try:
                from backend.core import engine_catalog as _ec
                for et in _ec.ENGINE_META:
                    await asyncio.to_thread(_ec.installed_versions, et)
                logger.info("引擎体积缓存预热完成")
            except Exception as e:
                logger.warning(f"引擎体积缓存预热失败: {e}")
        app['warm_task'] = asyncio.create_task(_warm_engine_size_cache())

    async def on_cleanup(app):
        if 'monitor_task' in app:
            app['monitor_task'].cancel()
        if 'restore_task' in app:
            app['restore_task'].cancel()
        if 'warm_task' in app:
            app['warm_task'].cancel()

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
