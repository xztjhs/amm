"""
AMM Model Manager
=================
混合架构模型管理器，统一管理 9 类模型的启停与监控，
支持在网页上选择 vllm / llama.cpp / diffusers 引擎。
"""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml
import psutil
import GPUtil
import aiofiles

from backend.core.engine import (
    BaseEngine, EngineRegistry, ModelInstance, EngineVersion,
)
from backend.core.version_manager import VersionManager, ENGINES_ROOT
from backend.core.port_allocator import PortAllocator
from backend.core.instance_store import InstanceStore
from backend.core.client_compat import derive_uid, derive_display_name, assess_model_id
from backend.engines import LlamaCppEngine, VllmEngine, DiffusersEngine, SentenceTransformersEngine

logger = logging.getLogger("AMM.Manager")

# 部署路径
AMM_ROOT = os.environ.get("AMM_ROOT", "/amm")
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
LOGS_DIR = os.path.join(AMM_ROOT, "logs")
# v1.0 新增：动态实例数据目录（不触碰任何现有固定路径）
DATA_DIR = os.path.join(AMM_ROOT, "data")
# 动态实例端口池（9 类固定场景的 18081-18089 不在其中）
DYNAMIC_PORT_START = int(os.environ.get("AMM_DYNAMIC_PORT_START", "18100"))
DYNAMIC_PORT_END = int(os.environ.get("AMM_DYNAMIC_PORT_END", "18499"))


class ModelManager:
    """
    混合引擎模型管理器。
    整合引擎注册、版本管理、模型实例生命周期。
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Dict = {}
        self.instances: Dict[str, ModelInstance] = {}

        # 初始化引擎注册中心
        self.registry = EngineRegistry()
        self.registry.register(LlamaCppEngine(ENGINES_ROOT))
        self.registry.register(VllmEngine(ENGINES_ROOT))
        self.registry.register(DiffusersEngine(ENGINES_ROOT))
        self.registry.register(SentenceTransformersEngine(ENGINES_ROOT))

        # 初始化版本管理器
        self.version_manager = VersionManager(self.registry)

        # v1.0: 动态实例端口分配器 + SQLite 实例存储
        self.port_allocator = PortAllocator(DYNAMIC_PORT_START, DYNAMIC_PORT_END)
        self.store = InstanceStore(os.path.join(DATA_DIR, "amm.db"))

        # 加载配置和状态
        self._load_config()
        self._load_state()

    # ================================================================
    # Config & State
    # ================================================================

    def _load_config(self):
        """加载模型配置 YAML"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        logger.info(f"配置已加载: {self.config_path}")

        # 端口冲突检测
        used_ports = {}
        for key in self._model_keys():
            if key in self.config:
                port = self.config[key]["port"]
                if port in used_ports:
                    logger.error(f"端口冲突: {key} (port {port}) 与 {used_ports[port]} 冲突")
                used_ports[port] = key
        logger.info(f"端口检查完成，{len(used_ports)} 个模型")

        # 初始化模型实例
        for key in self._model_keys():
            if key in self.config:
                model_cfg = self.config[key]
                inst = ModelInstance(
                    model_id=model_cfg["id"],
                    name=model_cfg["name"],
                    category=model_cfg["category"],
                    port=model_cfg["port"],
                    selected_model_file=model_cfg.get("available_models", [{}])[0].get("file", ""),
                    parameters={p["name"]: p["default"] for p in model_cfg.get("parameters", [])},
                    # 引擎选择: 优先使用配置的 engine_type，否则根据类别推荐
                    engine_type=model_cfg.get("engine_type", ""),
                )
                if not inst.engine_type:
                    engine = self.registry.get_recommended(model_cfg["category"])
                    if engine:
                        inst.engine_type = engine.engine_type
                self.instances[model_cfg["id"]] = inst
                # 固定场景端口不进动态池
                self.port_allocator.reserve(model_cfg["port"])

    @staticmethod
    def _model_keys() -> List[str]:
        return [
            "chat_model", "embedding_model", "asr_model", "tts_model",
            "reranker_model", "ocr_model", "t2i_model", "t2v_model", "i2v_model",
        ]

    # ================================================================
    # Startup Command (v0.6) : 人工编排启动命令/脚本
    # ================================================================
    STARTUP_DIR = os.path.join(AMM_ROOT, "scripts")

    def _startup_script_path(self, model_id: str) -> str:
        return os.path.join(self.STARTUP_DIR, f"{model_id}.sh")

    async def build_command_preview(self, model_id: str) -> Dict[str, Any]:
        """基于当前参数+引擎，生成实际启动命令 (供 UI 预览/编辑)"""
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        model_cfg = self._find_model_config(model_id)
        if not model_cfg:
            return {"error": f"模型配置 {model_id} 未找到"}
        engine = self.registry.get(inst.engine_type)
        if not engine:
            return {"error": f"引擎 {inst.engine_type} 不支持"}
        host = self.config.get("server", {}).get("host", "0.0.0.0")
        try:
            cmd = await engine.build_command(model_cfg, inst, MODELS_DIR, host)
            return {
                "engine": inst.engine_type,
                "engine_version": inst.engine_version or "",
                "model": inst.selected_model_file,
                "port": model_cfg.get("port"),
                "args": cmd,
                "shell": self._shell_join(cmd),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _shell_join(cmd: list) -> str:
        import shlex
        return " ".join(shlex.quote(c) for c in cmd)

    def get_startup_command(self, model_id: str) -> Dict[str, Any]:
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        return {
            "model_id": model_id,
            "startup_command": inst.startup_command or "",
            "has_custom": bool(inst.startup_command and inst.startup_command.strip()),
            "script_path": self._startup_script_path(model_id),
        }

    def save_startup_command(self, model_id: str, command: str, write_script: bool = True) -> Dict[str, Any]:
        """保存自定义启动命令；写入 .sh 脚本并持久化"""
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        command = (command or "").strip()
        inst.startup_command = command
        if write_script:
            try:
                os.makedirs(self.STARTUP_DIR, exist_ok=True)
                script = self._startup_script_path(model_id)
                with open(script, "w", encoding="utf-8") as f:
                    f.write("#!/bin/bash\n# AMM 自定义启动脚本 (人工编辑启动命令)\nset -e\n" + command + "\n")
                os.chmod(script, 0o755)
            except Exception as e:
                logger.warning(f"写入启动脚本失败: {e}")
        self._save_state()
        logger.info(f"模型 {model_id} 启动命令已保存")
        return {"success": True, "command": command, "has_custom": bool(command)}

    def clear_startup_command(self, model_id: str) -> Dict[str, Any]:
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        inst.startup_command = ""
        try:
            p = self._startup_script_path(model_id)
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass
        self._save_state()
        logger.info(f"模型 {model_id} 启动命令已清除")
        return {"success": True, "has_custom": False}

    def _load_state(self):
        """从磁盘恢复参数、模型文件和引擎选择；并恢复动态实例 (v1.0)"""
        state_file = Path(LOGS_DIR) / "state.json"
        if state_file.exists():
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                for model_id, saved in state.get("instances", {}).items():
                    if model_id in self.instances:
                        if saved.get("parameters"):
                            self.instances[model_id].parameters.update(saved["parameters"])
                        if saved.get("selected_model_file"):
                            self.instances[model_id].selected_model_file = saved["selected_model_file"]
                        if saved.get("engine_type"):
                            self.instances[model_id].engine_type = saved["engine_type"]
                        if saved.get("engine_version"):
                            self.instances[model_id].engine_version = saved["engine_version"]
                        if saved.get("flavor"):
                            self.instances[model_id].flavor = saved["flavor"]
                        if saved.get("startup_command") is not None:
                            self.instances[model_id].startup_command = saved["startup_command"]
                logger.info(f"状态已恢复: {len(state.get('instances', {}))} 个模型")
            except Exception as e:
                logger.warning(f"恢复状态失败: {e}")

        self._restore_dynamic_instances()

    # ================================================================
    # v1.0 Dynamic Instances (running-model 模式)
    # ================================================================
    def _restore_dynamic_instances(self):
        """从 SQLite 恢复动态实例定义（进程已不在，状态置为 stopped）"""
        try:
            rows = self.store.list_all(dynamic_only=True)
        except Exception as e:
            logger.warning(f"恢复动态实例失败: {e}")
            return
        restored = 0
        for row in rows:
            data = row.get("data") or {}
            uid = row.get("uid") or data.get("model_id")
            if not uid or uid in self.instances:
                continue
            inst = ModelInstance(
                model_id=uid,
                name=data.get("name") or uid,
                category=row.get("category") or data.get("category", ""),
                port=row.get("port") or 0,
                engine_type=data.get("engine_type", ""),
                engine_version=data.get("engine_version", ""),
                status="stopped",
                selected_model_file=data.get("selected_model_file", ""),
                parameters=dict(data.get("parameters") or {}),
                startup_command=data.get("startup_command", ""),
                flavor=data.get("flavor", ""),
                replicas=int(data.get("replicas") or 1),
                is_dynamic=True,
                model_cfg=dict(data.get("model_cfg") or {}),
            )
            self.instances[uid] = inst
            if inst.port:
                self.port_allocator.reserve(inst.port)
            restored += 1
        if restored:
            logger.info(f"已恢复 {restored} 个动态实例定义")

    def _persist_instance(self, inst: ModelInstance, desired_status: str = ""):
        """把实例快照写入 SQLite"""
        try:
            data = {
                "model_id": inst.model_id,
                "name": inst.name,
                "category": inst.category,
                "engine_type": inst.engine_type,
                "engine_version": inst.engine_version,
                "flavor": inst.flavor,
                "replicas": inst.replicas,
                "selected_model_file": inst.selected_model_file,
                "parameters": inst.parameters,
                "startup_command": inst.startup_command,
                "model_cfg": inst.model_cfg,
                "desired_status": desired_status or inst.status,
            }
            self.store.upsert(
                uid=inst.model_id,
                category=inst.category,
                port=inst.port,
                status=inst.status,
                is_dynamic=inst.is_dynamic,
                data=data,
            )
        except Exception as e:
            logger.warning(f"实例持久化异常 ({inst.model_id}): {e}")

    async def restore_running_instances(self):
        """服务启动后自动拉起之前处于 running 状态的动态实例"""
        targets = []
        for inst in self.instances.values():
            if not inst.is_dynamic:
                continue
            row = self.store.get(inst.model_id)
            if not row:
                continue
            desired = (row.get("data") or {}).get("desired_status")
            if desired == "running":
                targets.append(inst.model_id)
        for uid in targets:
            logger.info(f"自动恢复运行中的动态实例: {uid}")
            await self.start_model(uid)

    async def launch_instance(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """创建并启动一个动态模型实例 (running-model 模式)

        spec: {
            model_uid, model_type/category, engine_type, engine_version,
            model_file, parameters, replicas, flavor, port(可选), model_name
        }
        """
        uid = str(spec.get("model_uid") or spec.get("uid") or "").strip()
        category = str(spec.get("model_type") or spec.get("category") or "").strip()
        engine_type = str(spec.get("engine_type") or "").strip()
        engine_version = str(spec.get("engine_version") or "").strip()
        model_file = str(spec.get("model_file") or spec.get("model") or "").strip()
        params = spec.get("parameters") or spec.get("params") or {}
        flavor = str(spec.get("flavor") or "").strip()
        name = str(spec.get("model_name") or spec.get("name") or "").strip()

        if not category:
            return {"error": "缺少 model_type (模型类别)"}
        if not engine_type:
            return {"error": "缺少 engine_type (推理引擎)"}
        if not uid:
            uid = self._generate_uid(category, model_file)
        if uid in self.instances:
            return {"error": f"实例 {uid} 已存在"}
        if not isinstance(params, dict):
            return {"error": "parameters 必须是对象"}

        # 常见误配：模型文件明显是 embedding/reranker，但类别选成了 chat，
        # 结果 /v1/embeddings 用不了（实例跑起来只是一个普通补全模型）。
        # 这里自动纠正类别并补上对应参数，同时在返回里说明，避免静默出错。
        auto_fixed = []
        f_low = model_file.lower()
        if engine_type == "llama_cpp":
            if category == "chat":
                if "embedding" in f_low and not params.get("embeddings"):
                    category = "embedding"
                    params = dict(params)
                    params["embeddings"] = True
                    auto_fixed.append("类别 chat → embedding（文件名含 embedding，已开启 embeddings）")
                elif "reranker" in f_low and not params.get("reranking"):
                    category = "reranker"
                    params = dict(params)
                    params["reranking"] = True
                    auto_fixed.append("类别 chat → reranker（文件名含 reranker，已开启 reranking）")
            # 类别正确但缺关键开关：llama-server 少了这些参数会直接回 501，
            # 客户端只看到「does not support embeddings」这种难懂的错误。
            if category == "embedding" and not params.get("embeddings"):
                params = dict(params)
                params["embeddings"] = True
                auto_fixed.append("已自动开启 embeddings（embedding 类别必需）")
            if category == "reranker" and not params.get("reranking"):
                params = dict(params)
                params["reranking"] = True
                auto_fixed.append("已自动开启 reranking（reranker 类别必需）")

        engine = self.registry.get(engine_type)
        if not engine:
            return {"error": f"未知引擎: {engine_type}"}
        if category not in engine.get_supported_categories():
            return {
                "error": f"引擎 {engine_type} 不支持 {category} 类别",
                "supported": engine.get_supported_categories(),
            }

        port = self.port_allocator.allocate(spec.get("port"))
        if port is None:
            return {"error": f"动态端口池耗尽 ({DYNAMIC_PORT_START}-{DYNAMIC_PORT_END})"}

        try:
            replicas = max(1, int(spec.get("replicas") or 1))
        except (TypeError, ValueError):
            replicas = 1

        model_cfg = {
            "id": uid,
            "name": name or derive_display_name(category, model_file, fallback=uid),
            "category": category,
            "port": port,
            "engine_type": engine_type,
            "available_models": [{"file": model_file}] if model_file else [],
            "parameters": [],
        }

        inst = ModelInstance(
            model_id=uid,
            name=model_cfg["name"],
            category=category,
            port=port,
            engine_type=engine_type,
            engine_version=engine_version,
            status="stopped",
            selected_model_file=model_file,
            parameters=dict(params),
            flavor=flavor,
            replicas=replicas,
            is_dynamic=True,
            model_cfg=model_cfg,
        )
        self.instances[uid] = inst
        self._persist_instance(inst, desired_status="running")

        result = await self.start_model(uid)
        if "error" in result:
            # 启动失败：保留实例定义（便于用户修参数后重启），释放端口
            self._persist_instance(inst, desired_status="stopped")
            result["model_uid"] = uid
            result["port"] = port
            if auto_fixed:
                result["auto_fixed"] = auto_fixed
            result["warnings"] = assess_model_id(uid, category)
            return result
        result["model_uid"] = uid
        result["port"] = port
        if auto_fixed:
            result["auto_fixed"] = auto_fixed
            logger.info(f"实例 {uid} 自动纠正: {auto_fixed}")
        warnings = assess_model_id(uid, category)
        if warnings:
            result["warnings"] = warnings
            logger.info(f"实例 {uid} 客户端兼容性提示: {warnings}")
        return result

    async def terminate_instance(self, model_id: str) -> Dict[str, Any]:
        """注销动态实例（停止 + 从注册表移除 + 释放端口）"""
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        if not inst.is_dynamic:
            return {"error": "固定场景实例不可注销，请使用 stop 接口"}
        await self.stop_model(model_id)
        self.instances.pop(model_id, None)
        self.port_allocator.release(inst.port)
        try:
            self.store.delete(model_id)
        except Exception as e:
            logger.warning(f"删除实例持久化记录失败 ({model_id}): {e}")
        logger.info(f"动态实例 {model_id} 已注销")
        return {"success": True, "model_uid": model_id}

    def get_running_models(self) -> List[Dict[str, Any]]:
        """运行中模型列表 (Xinference running-model 风格)"""
        self.refresh_instances()
        result = []
        for inst in self.instances.values():
            if inst.status not in ("running", "starting"):
                continue
            result.append({
                "model_uid": inst.model_id,
                "model_name": inst.name,
                "model_type": inst.category,
                "engine_type": inst.engine_type,
                "engine_version": inst.engine_version,
                "flavor": inst.flavor,
                "port": inst.port,
                "status": inst.status,
                "pid": inst.pid,
                "replicas": inst.replicas,
                "model_file": inst.selected_model_file,
                "uptime_seconds": inst.uptime_seconds,
                "gpu_memory_mb": inst.gpu_memory_mb,
                "memory_mb": inst.memory_mb,
                "cpu_percent": inst.cpu_percent,
                "request_count": inst.request_count,
                "is_dynamic": inst.is_dynamic,
            })
        return result

    def _generate_uid(self, category: str, model_file: str) -> str:
        """生成不冲突的实例 uid。

        客户端（Cherry Studio 等）会拿 uid 去模型名注册表做**归一化模糊匹配**
        （去掉 -14b/-0.6b 这类尺寸后缀再比），所以 uid 必须自带模型身份与类别：
          - 好: qwen3-embedding-0.6b → 命中 qwen3-embedding-0-6b（name/能力都对）
          - 坏: qwen3                → 命中 qwen3-14b（被认成聊天模型）
          - 坏: embedding-...-Q8_0   → 匹配不到（被当成无能力自定义模型）
        """
        base = derive_uid(category, model_file)
        uid = base
        idx = 1
        while uid in self.instances:
            idx += 1
            uid = f"{base}-{idx}"
        return uid

    def _save_state(self):
        """持久化当前参数、模型文件和引擎选择"""
        try:
            state = {"instances": {}}
            for model_id, inst in self.instances.items():
                state["instances"][model_id] = {
                    "parameters": inst.parameters,
                    "selected_model_file": inst.selected_model_file,
                    "engine_type": inst.engine_type,
                    "engine_version": inst.engine_version,
                    "flavor": inst.flavor,
                    "startup_command": inst.startup_command,
                }
            state_file = Path(LOGS_DIR) / "state.json"
            os.makedirs(LOGS_DIR, exist_ok=True)
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            # v1.0: 动态实例同时写入 SQLite（重启后可恢复）
            for inst in self.instances.values():
                if inst.is_dynamic:
                    self._persist_instance(inst)
        except Exception as e:
            logger.warning(f"保存状态失败: {e}")

    # ================================================================
    # Engine Management
    # ================================================================

    def get_engines_for_category(self, category: str) -> Dict[str, BaseEngine]:
        """获取支持某类别的引擎列表"""
        return self.registry.list_for_category(category)

    def get_engine_info(self) -> Dict[str, Any]:
        """获取所有引擎信息"""
        result = {}
        for engine_type, engine in self.registry.list_all().items():
            result[engine_type] = {
                "display_name": engine.get_display_name(),
                "description": engine.get_description(),
                "supported_categories": engine.get_supported_categories(),
            }
        return result

    async def get_engine_versions(self) -> Dict[str, List[Dict]]:
        """获取所有引擎的版本列表"""
        all_versions = await self.version_manager.list_all_versions()
        result = {}
        for engine_type, versions in all_versions.items():
            result[engine_type] = []
            for v in versions:
                result[engine_type].append({
                    "version": v.version,
                    "flavor": v.flavor,
                    "status": v.status,
                    "install_path": v.install_path,
                    "binary_path": v.binary_path,
                    "is_default": v.is_default,
                    "metadata": v.metadata or {},
                })
        return result

    async def install_engine_version(self, engine_type: str, version: str, flavor: str = ""):
        """安装引擎版本"""
        task = await self.version_manager.install(engine_type, version, flavor)
        return {
            "engine_type": task.engine_type,
            "version": task.version,
            "flavor": task.flavor,
            "status": task.status,
            "message": task.message,
        }

    async def uninstall_engine_version(self, engine_type: str, version: str, flavor: str = ""):
        """卸载引擎版本"""
        return await self.version_manager.uninstall(engine_type, version, flavor)

    def get_install_task(self, engine_type: str, version: str, flavor: str = ""):
        """获取安装任务进度"""
        return self.version_manager.get_install_task(engine_type, version, flavor)

    # ================================================================
    # Model Instance Management
    # ================================================================

    def get_config(self) -> Dict:
        return self.config

    def get_instance(self, model_id: str) -> Optional[ModelInstance]:
        return self.instances.get(model_id)

    def get_all_instances(self) -> Dict[str, ModelInstance]:
        return self.instances

    def update_parameters(self, model_id: str, params: Dict[str, Any]):
        inst = self.instances.get(model_id)
        if not inst:
            return
        inst.parameters.update(params)
        self._save_state()
        logger.info(f"模型 {model_id} 参数已更新: {params}")

    # 哪些 model_cfg 字段会被 UI 当作"FP8 / 显存优化"高级设置透传到 yaml (2026-08-05)
    # 这些字段不在 instance.parameters 里, 但 UI 也需要能在网页上切换
    # 2026-08-08: 增加 offload 策略 (gpu|model|group), 解决 t2v/i2v GPU 不工作
    ADVANCED_FIELDS = {"quant", "compute_dtype", "boundary_ratio", "cpu_offload", "offload"}

    def update_advanced_settings(self, model_id: str, settings: Dict[str, Any]):
        """更新 Diffusers 引擎的高级设置 (FP8 量化 / CPU offload / boundary 等) 到 yaml

        支持字段: quant, compute_dtype, boundary_ratio, cpu_offload, offload
        其他字段会被拒绝 (防止前端误改 category / model_id 等核心字段)
        """
        # 找到 yaml key
        cfg_key = None
        cfg = None
        for k, v in self.config.items():
            if isinstance(v, dict) and v.get("id") == model_id:
                cfg_key = k
                cfg = v
                break
        if cfg is None:
            raise ValueError(f"未找到模型 {model_id} 配置")

        # 白名单校验
        bad = set(settings.keys()) - self.ADVANCED_FIELDS
        if bad:
            raise ValueError(f"advanced_settings 不支持字段 {bad}, 仅支持 {self.ADVANCED_FIELDS}")

        # 类型校验
        for f, v in settings.items():
            if f == "quant":
                v = (v or "").lower().strip()
                if v not in ("fp8", "bf16", "none", "off", ""):
                    raise ValueError(f"quant 值不合法 {v!r}, 允许: fp8/bf16/none/off/空")
                cfg["quant"] = v or None
            elif f == "compute_dtype":
                v = (v or "").lower().strip()
                if v not in ("bf16", "fp16", "fp32", ""):
                    raise ValueError(f"compute_dtype 值不合法 {v!r}")
                cfg["compute_dtype"] = v or None
            elif f == "boundary_ratio":
                if v is None or v == "":
                    cfg["boundary_ratio"] = None
                else:
                    try:
                        br = float(v)
                        if not (0.0 < br < 1.0):
                            raise ValueError("boundary_ratio 必须在 (0,1) 之间")
                        cfg["boundary_ratio"] = br
                    except (TypeError, ValueError) as e:
                        raise ValueError(f"boundary_ratio 必须是 float: {e}")
            elif f == "cpu_offload":
                cfg["cpu_offload"] = bool(v)
            elif f == "offload":
                v = (v or "").lower().strip()
                if v not in ("gpu", "model", "group", ""):
                    raise ValueError(f"offload 值不合法 {v!r}, 允许: gpu/model/group")
                cfg["offload"] = v or None

        # 写回 yaml
        import yaml as _yaml
        with open(self.config_path, "w", encoding="utf-8") as f:
            _yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        logger.info(f"模型 {model_id} 高级设置已写 yaml: {settings}")

    def get_advanced_settings(self, model_id: str) -> Dict[str, Any]:
        """从 yaml 读取 advanced settings (source of truth, 不用 state.json cache)"""
        for k, v in self.config.items():
            if isinstance(v, dict) and v.get("id") == model_id:
                return {f: v.get(f) for f in self.ADVANCED_FIELDS}
        raise ValueError(f"未找到模型 {model_id} 配置")

    def update_model_file(self, model_id: str, model_file: str):
        inst = self.instances.get(model_id)
        if not inst:
            return
        inst.selected_model_file = model_file
        self._save_state()
        logger.info(f"模型 {model_id} 文件已切换: {model_file}")

    def update_engine(self, model_id: str, engine_type: str, engine_version: str = ""):
        """更新模型使用的引擎"""
        inst = self.instances.get(model_id)
        if not inst:
            return
        # 验证引擎是否支持此类别
        engine = self.registry.get(engine_type)
        if not engine:
            raise ValueError(f"未知引擎: {engine_type}")
        if inst.category not in engine.get_supported_categories():
            raise ValueError(f"引擎 {engine_type} 不支持 {inst.category} 类别")

        inst.engine_type = engine_type
        inst.engine_version = engine_version
        self._save_state()
        logger.info(f"模型 {model_id} 引擎切换: {engine_type}/{engine_version}")

    def _preflight_model_file(self, inst) -> Dict[str, Any]:
        """启动前检查模型文件是否存在，并在缺文件时给出替代建议。"""
        f = (inst.selected_model_file or "").strip()
        if not f:
            # 未配置模型文件交给引擎/模版流程处理（模版部署是先建实例再填文件），
            # 这里不拦截，否则会打断正常的部署链路。
            return {}
        models_dir = os.environ.get("MODELS_DIR", "/models")
        path = f if os.path.isabs(f) else os.path.join(models_dir, f)
        if os.path.exists(path):
            return {}

        # 不存在 -> 在同目录里找候选，帮助用户一键改对
        import glob as _glob
        parent = os.path.dirname(path)
        cands = []
        if os.path.isdir(parent):
            for p in sorted(_glob.glob(os.path.join(parent, "*.gguf"))):
                cands.append(os.path.relpath(p, models_dir))
        msg = f"模型文件不存在: {f}"
        if cands:
            msg += "；同目录下可用: " + "、".join(os.path.basename(c) for c in cands[:4])
        return {
            "error": msg,
            "model_file": f,
            "candidates": cands,
            "hint": "在 Dashboard 里双击该模型，用「模型文件」一栏改成实际存在的文件",
        }

    async def wait_until_ready(self, model_id: str,
                               timeout: float = None) -> Dict[str, Any]:
        """等实例 HTTP 端口能一听请求（#3）

        返 ``{"ready": bool, "ready_wait_ms": int}``，绝不抛异常。
        超时不算启动失败（进程可能在慢加载），只是告诉调用方尚未就绪。
        """
        if timeout is None:
            try:
                timeout = float(os.environ.get("AMM_LAUNCH_READY_TIMEOUT", "30"))
            except ValueError:
                timeout = 30.0
        if timeout <= 0:
            return {"ready": None, "ready_wait_ms": 0}

        inst = self.instances.get(model_id)
        if not inst or not inst.port:
            return {"ready": None, "ready_wait_ms": 0}

        t0 = time.time()
        deadline = t0 + timeout
        while time.time() < deadline:
            if inst.process is not None and inst.process.returncode is not None:
                # 进程已退出，不存在“等就绪”了
                return {"ready": False, "ready_wait_ms": int((time.time() - t0) * 1000),
                        "ready_error": "进程已退出"}
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", inst.port), timeout=1.0)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                ms = int((time.time() - t0) * 1000)
                logger.info(f"实例 {model_id} 端口 {inst.port} 已就绪（{ms}ms）")
                return {"ready": True, "ready_wait_ms": ms}
            except Exception:
                await asyncio.sleep(0.25)
        ms = int((time.time() - t0) * 1000)
        logger.warning(f"实例 {model_id} 端口 {inst.port} 在 {timeout}s 内未就绪")
        return {"ready": False, "ready_wait_ms": ms,
                "ready_error": f"端口在 {int(timeout)}s 内未就绪"}

    async def start_model(self, model_id: str):
        """启动模型推理服务（通过所选引擎）"""
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}
        if inst.status == "running":
            return {"error": f"模型 {model_id} 已在运行中"}

        # 查找模型配置
        model_cfg = self._find_model_config(model_id)
        if not model_cfg:
            return {"error": f"模型配置 {model_id} 未找到"}

        # 获取引擎
        engine = self.registry.get(inst.engine_type)
        if not engine:
            return {"error": f"引擎 {inst.engine_type} 不支持"}

        # 启动前校验模型文件：GGUF 目录里常见「配的是 Q4_K_M，实际只下了 Q2_K」，
        # 不校验的话 llama-server 会启动后立刻退出，用户只看到一句
        # "failed to load model"，很难判断是文件不存在还是文件损坏。
        check = self._preflight_model_file(inst)
        if check.get("error"):
            # 与「引擎启动失败」保持一致：实例标记为 error，界面能直接看出问题
            inst.status = "error"
            try:
                self._save_state()
            except Exception:
                pass
            return check

        try:
            inst.status = "starting"
            inst.start_time = time.time()

            # 构建命令：优先使用人工自定义启动命令(脚本)，否则按参数自动生成
            host = self.config.get("server", {}).get("host", "0.0.0.0")
            if inst.startup_command and inst.startup_command.strip():
                cmd = ["/bin/bash", self._startup_script_path(model_id)]
                logger.info(f"模型 {model_id} 使用自定义启动脚本: {self._startup_script_path(model_id)}")
            else:
                cmd = await engine.build_command(model_cfg, inst, MODELS_DIR, host)

            if cmd:
                # 子进程模式 (llama.cpp, vllm)
                log_path = os.path.join(LOGS_DIR, f"{model_id}_server.log")
                inst.process = await engine.start_process(cmd, log_path)
                inst.pid = inst.process.pid
            else:
                # 内置桥接模式 (diffusers)。默认不预加载(避免占用满显存);
                # 若 AMM_START_PRELOAD=1 则按当前高级参数后台预加载到 GPU (v0.7.3)
                inst.pid = 0
                if (
                    os.environ.get("AMM_START_PRELOAD", "0").strip() in ("1","true","on")
                    and model_cfg.get("engine_type", "") == "diffusers"
                    and model_id in ("t2i", "t2v", "i2v")
                ):
                    asyncio.create_task(self._preload_diffusers(model_id, model_cfg))

            inst.status = "running"
            # 兜底回填 flavor：界面上要能看出用的是 cuda 还是 cpu 构建。
            # 引擎层（llama_cpp）会在 build_command 里填真实值；
            # vLLM / Diffusers 只有 cuda 构建，这里补上。
            if not (getattr(inst, "flavor", "") or "").strip():
                if inst.engine_type in ("vllm", "diffusers", "sentence_transformers"):
                    inst.flavor = "cuda"
            logger.info(
                f"模型 {model_id} 已启动, engine={inst.engine_type}, "
                f"ver={inst.engine_version or '-'}, flavor={inst.flavor or '-'}, PID={inst.pid}")
            self._persist_instance(inst, desired_status="running")

            # #3：等端口真的能接管请求再返回。
            # 以前返回 running 后客户端立刻发请求会瞬时 500（端口未就绪）。
            # 默认等 30s，可用 AMM_LAUNCH_READY_TIMEOUT 调整；0 = 不等（旧行为）。
            ready = await self.wait_until_ready(model_id)
            result = {"success": True, "model_id": model_id, "status": inst.status,
                      "pid": inst.pid, "engine": inst.engine_type}
            result.update(ready)
            return result

        except Exception as e:
            inst.status = "error"
            logger.error(f"启动模型 {model_id} 失败: {e}")
            self._persist_instance(inst, desired_status="stopped")
            return {"error": str(e)}

    async def _preload_diffusers(self, model_id: str, model_cfg: Dict):
        """按当前 advanced 参数后台预加载 diffusers pipeline (start 后立即可用)"""
        try:
            from backend.api import diffusers_bridge as br
            t0 = time.time()
            await br._load_pipeline(model_cfg)
            logger.info(f"模型 {model_id} 预加载完成 ({(time.time()-t0):.1f}s)")
        except Exception as e:
            logger.warning(f"模型 {model_id} 预加载失败(可忽略, 首次推理会重试): {e}")

    async def stop_model(self, model_id: str):
        """停止模型推理服务"""
        inst = self.instances.get(model_id)
        if not inst:
            return {"error": f"模型 {model_id} 未找到"}

        try:
            engine = self.registry.get(inst.engine_type)
            if engine and inst.process:
                await engine.stop_process(inst)

            inst.status = "stopped"
            inst.process = None
            inst.pid = None
            inst.start_time = None
            inst.uptime_seconds = 0.0
            logger.info(f"模型 {model_id} 已停止")
            self._persist_instance(inst, desired_status="stopped")
            return {"success": True, "model_id": model_id, "status": "stopped"}

        except Exception as e:
            logger.error(f"停止模型 {model_id} 失败: {e}")
            return {"error": str(e)}

    async def restart_model(self, model_id: str):
        await self.stop_model(model_id)
        await asyncio.sleep(2)
        return await self.start_model(model_id)

    def _find_model_config(self, model_id: str) -> Optional[Dict]:
        # 动态实例：直接用实例自带的配置
        inst = self.instances.get(model_id)
        if inst is not None and inst.is_dynamic and inst.model_cfg:
            return inst.model_cfg
        for key in self._model_keys():
            if key in self.config and self.config[key]["id"] == model_id:
                return self.config[key]
        return None

    # ================================================================
    # Monitoring
    # ================================================================

    def get_gpu_info(self) -> List[dict]:
        """获取 GPU 信息。GPUtil 提供基础数据，叠加 nvidia-smi 补充风扇/Utilization 各子项/时钟等。
        nvidia-smi 子进程调用有 2s 缓存，避免高频轮询拖阻塞事件循环。"""
        import time as _t
        _cache = getattr(self, "_gpu_cache", None)
        now = _t.time()
        if _cache and now - _cache[0] < 2.0:
            return _cache[1]
        gpus = []
        try:
            # nvidia-smi 查询更多字段（风扇、各利用率、时钟、功耗）
            smi_extra = self._nvidia_smi_query()
            compute_apps = self._nvidia_smi_compute_apps() if len(smi_extra) > 0 else {}
            for gpu in GPUtil.getGPUs():
                ext = smi_extra.get(gpu.id, {})
                gpus.append({
                    "id": gpu.id,
                    "name": gpu.name,
                    "load": round(gpu.load * 100, 1),
                    # Utilization 各子项 (%)
                    "util_sm": ext.get("util_gpu", round(gpu.load * 100, 1)),  # 计算单元利用率
                    "util_mem": ext.get("util_mem", None),
                    "util_enc": ext.get("util_enc", None),
                    "util_dec": ext.get("util_dec", None),
                    "memory_used_mb": gpu.memoryUsed,
                    "memory_total_mb": gpu.memoryTotal,
                    "memory_percent": round(gpu.memoryUtil * 100, 1),
                    "temperature": gpu.temperature,
                    "fan_speed": ext.get("fan", None),
                    "power_draw": getattr(gpu, 'powerDraw', 0) or ext.get("power_draw", 0),
                    "power_limit": getattr(gpu, 'powerLimit', 0) or ext.get("power_limit", 0),
                    "clocks_sm": ext.get("clocks_sm", None),
                    "clocks_mem": ext.get("clocks_mem", None),
                    "pcie": ext.get("pcie_gen", None),
                    "gpu_uuid": ext.get("gpu_uuid", None),
                    # 当前在此 GPU 上运行的进程(完整命令) (v0.5)
                    "running_processes": compute_apps.get(gpu.id, []),
                })
        except Exception as e:
            logger.warning(f"获取 GPU 信息失败: {e}")
        self._gpu_cache = (now, gpus)
        return gpus

    def _nvidia_smi_compute_apps(self) -> dict:
        """调用 nvidia-smi --query-compute-apps 获取各 GPU 上运行的进程(pid+完整命令+显存)。
        返回: {gpu_index: [{pid, process_name, used_memory_mb, command}]}，失败返回空 dict。"""
        import subprocess as _sp
        result = {}
        try:
            # 先用 gpu_bus_id 拿到 gpu_id -> uuid 映射（注意 -g 只对 compute-apps 有效）
            out = _sp.check_output(
                ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory,process_name",
                 "--format=csv,noheader,nounits"],
                stderr=_sp.DEVNULL, timeout=5, text=True,
            )
            # gpu_uuid -> index 映射（用于对应到 gpus）
            uuid_to_idx = {}
            q = _sp.check_output(
                ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"],
                stderr=_sp.DEVNULL, timeout=5, text=True,
            )
            for line in q.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        uuid_to_idx[parts[1]] = int(parts[0])
                    except ValueError:
                        pass
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                gpu_uuid, pid, mem, name = parts[0], parts[1], parts[2], parts[3]
                gid = uuid_to_idx.get(gpu_uuid)
                if gid is None:
                    continue
                # 进程完整命令行
                cmdline = ""
                try:
                    import psutil
                    cmdline = " ".join(psutil.Process(int(pid)).cmdline())
                except Exception:
                    cmdline = name
                # 容器内看不到宿主 PID，nvidia-smi 会返回 [Not Found]/[N/A]；
                # 这种情况标注为「容器外进程」，避免用户误以为进程异常
                if not cmdline or cmdline.strip() in ("[Not Found]", "[N/A]", "N/A"):
                    cmdline = f"容器外进程（宿主 PID {pid}）"
                result.setdefault(gid, []).append({
                    "pid": pid,
                    "name": name,
                    "gpu_memory_mb": mem,
                    "command": cmdline or name,
                })
        except Exception as e:
            logger.debug(f"compute-apps 查询失败(降级): {e}")
        return result

    def _nvidia_smi_query(self) -> dict:
        """调用 nvidia-smi 获取 GPU 序号 -> 字段 映射。失败返回空 dict。"""
        import subprocess as _sp
        result = {}
        try:
            query = ("index,name,temperature.gpu,utilization.gpu,utilization.memory,"
                     "utilization.encoder,utilization.decoder,fan.speed,power.draw,"
                     "power.limit,clocks.sm,clocks.mem,pcie.link.gen.gpucurrent,gpu_uuid")
            out = _sp.check_output(
                ["nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits"],
                stderr=_sp.DEVNULL, timeout=5, text=True,
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 13:
                    continue
                try:
                    gid = int(parts[0])
                except ValueError:
                    continue
                def _f(s):
                    try:
                        return round(float(s), 1) if s and s.lower() not in ("n/a", "", "[n/a]") else None
                    except Exception:
                        return None
                result[gid] = {
                    "name_full": parts[1],
                    "temp": _f(parts[2]),
                    "util_gpu": _f(parts[3]),
                    "util_mem": _f(parts[4]),
                    "util_enc": _f(parts[5]),
                    "util_dec": _f(parts[6]),
                    "fan": _f(parts[7]),
                    "power_draw": _f(parts[8]),
                    "power_limit": _f(parts[9]),
                    "clocks_sm": _f(parts[10]),
                    "clocks_mem": _f(parts[11]),
                    "pcie_gen": _f(parts[12]),
                    "gpu_uuid": parts[13],
                }
        except Exception as e:
            logger.debug(f"nvidia-smi 查询失败(降级): {e}")
        return result

    def get_system_info(self) -> Dict:
        # 非阻塞采样: interval=None 返回自上次调用以来均值, 不阻塞事件循环
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
        except Exception:
            cpu_percent = 0.0
        try:
            mem = psutil.virtual_memory()
        except Exception:
            mem = None
        try:
            disk = psutil.disk_usage('/')
        except Exception:
            disk = None
        return {
            "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else 0.0,
            "cpu_count": psutil.cpu_count() or 0,
            "memory_total_gb": round(mem.total / (1024**3), 2) if mem else 0.0,
            "memory_used_gb": round(mem.used / (1024**3), 2) if mem else 0.0,
            "memory_percent": round(mem.percent, 1) if mem else 0.0,
            "disk_total_gb": round(disk.total / (1024**3), 2) if disk else 0.0,
            "disk_used_gb": round(disk.used / (1024**3), 2) if disk else 0.0,
            "disk_percent": round(disk.percent, 1) if disk else 0.0,
            "gpus": self.get_gpu_info(),
            "cpu_procs": self.get_cpu_inference_procs(),
            "uptime_seconds": time.time() - psutil.boot_time(),
        }

    # CPU 推理进程识别：命令行里出现这些特征即视为「在跑推理的进程」。
    # 覆盖 llama.cpp（llama-server/llama-tts/llama-cli）、transformers、
    # sentence-transformers、diffusers、python 侧的推理脚本。
    _CPU_PROC_PATTERNS = (
        "llama-server", "llama-tts", "llama-cli", "llama-bench", "llama-quantize",
        "llama-server-cpu", "server.py", "vllm", "text-embeddings",
        "sentence_transformers", "diffusers", "embedding_server",
        "amm_engine", "xinference",
    )
    # 明显不是推理的进程（避免把编辑器/构建工具误判进来）
    _CPU_PROC_EXCLUDE = (
        "grep", "ps -ef", "nvidia-smi", "docker exec", "vim", "nano",
        "git ", "pip install", "apt ", "yum ",
    )

    def get_cpu_inference_procs(self) -> List[Dict[str, Any]]:
        """列出正在用 CPU 做推理的进程（PID / 程序 / CPU / 内存）。

        只读 /proc + psutil，不依赖 nvidia-smi，容器内也能工作。
        结果按 CPU 占用从高到低排序，最多返回 30 条。
        前端默认 0.5s 轮询 /api/system，这里做 2s 缓存，避免频繁全表扫描。
        """
        now = time.time()
        cached = getattr(self, "_cpu_proc_cache", None)
        if cached and now - cached[0] < 2.0:
            return cached[1]
        out: List[Dict[str, Any]] = []
        try:
            procs = psutil.process_iter(
                ["pid", "name", "cmdline", "cpu_percent", "memory_info",
                 "num_threads", "create_time", "username"]
            )
        except Exception as e:
            logger.warning(f"进程枚举失败: {e}")
            return out

        own_pid = os.getpid()
        for p in procs:
            try:
                info = p.info
                pid = info.get("pid")
                if not pid or pid == own_pid:
                    continue
                cmd_list = info.get("cmdline") or []
                cmd = " ".join(cmd_list) if cmd_list else (info.get("name") or "")
                if not cmd:
                    continue
                low = cmd.lower()
                if any(x in low for x in self._CPU_PROC_EXCLUDE):
                    continue
                name = info.get("name") or (cmd_list[0] if cmd_list else "")
                # 命中的特征：二进制名或命令行
                if not any(pat in low or pat in (name or "").lower()
                           for pat in self._CPU_PROC_PATTERNS):
                    continue
                mem_info = info.get("memory_info")
                rss_mb = round(mem_info.rss / (1024**2), 1) if mem_info else 0.0
                try:
                    cpu = round(p.cpu_percent(interval=None), 1)
                except Exception:
                    cpu = 0.0
                is_gpu = self._proc_uses_gpu(low)
                out.append({
                    "pid": pid,
                    "name": name,
                    "command": cmd[:300],
                    "cpu_percent": cpu,
                    "memory_mb": rss_mb,
                    "threads": info.get("num_threads") or 0,
                    "user": info.get("username") or "",
                    "uptime_seconds": int(time.time() - (info.get("create_time") or time.time())),
                    "uses_gpu": bool(is_gpu),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        out.sort(key=lambda r: (r["cpu_percent"], r["memory_mb"]), reverse=True)
        out = out[:30]
        self._cpu_proc_cache = (now, out)
        return out

    @staticmethod
    def _proc_uses_gpu(cmd_low: str) -> bool:
        """判断推理进程走 GPU 还是纯 CPU。

        判定顺序（后者覆盖前者）：
          1. 引擎目录/二进制带 -cpu（b12433-cpu）→ CPU
          2. -ngl 0 / --n-gpu-layers 0 / -ngl=0 → CPU
          3. 其它带 -ngl N>0、cuda、gpu 字样 → GPU
          4. 都没有 → 视为 CPU（llama.cpp 默认 n_gpu_layers=0）
        """
        import re as _re
        if _re.search(r"(^|[/\s-])cpu([/\s-]|$)", cmd_low) and "-cpu" in cmd_low:
            return False
        m = _re.search(r"(?:-ngl|--n-gpu-layers|--gpu-layers)[\s=]+(-?\d+)", cmd_low)
        if m:
            try:
                return int(m.group(1)) != 0
            except ValueError:
                pass
        return ("cuda" in cmd_low) or bool(_re.search(r"\bgpu\b", cmd_low))

    def refresh_instances(self):
        # 节流: 最近 0.5s 内已刷新则跳过，防 monitor_loop + 前端高频并发叠加
        import time as _t
        now = _t.time()
        _last = getattr(self, "_last_refresh", 0)
        if now - _last < 0.5:
            return
        self._last_refresh = now
        # 非阻塞刷新: 避免 interval=0.1 的 sync sleep 拖死事件循环
        for inst in self.instances.values():
            if not inst.process:
                continue
            try:
                retcode = inst.process.returncode
            except Exception:
                retcode = None
            if retcode is not None:
                inst.status = "error" if retcode != 0 else "stopped"
                inst.process = None
                continue
            inst.status = "running"
            if inst.start_time:
                inst.uptime_seconds = int(time.time() - inst.start_time)
            try:
                if inst.pid and inst.pid > 0:
                    proc = psutil.Process(inst.pid)
                    inst.memory_mb = proc.memory_info().rss / (1024**2)
                    # 瞬时采样(基于上次快照) 不阻塞
                    try:
                        inst.cpu_percent = round(proc.cpu_percent(interval=None), 1)
                    except Exception:
                        inst.cpu_percent = 0.0
            except (psutil.NoSuchProcess, Exception):
                inst.cpu_percent = 0.0

    async def get_model_logs(self, model_id: str, lines: int = 100) -> List[str]:
        log_file = Path(LOGS_DIR) / f"{model_id}_server.log"
        if log_file.exists():
            async with aiofiles.open(log_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                return content.strip().split('\n')[-lines:]
        return []

    async def get_server_logs(self, lines: int = 100) -> List[str]:
        log_file = Path(LOGS_DIR) / "amm_server.log"
        if log_file.exists():
            async with aiofiles.open(log_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                return content.strip().split('\n')[-lines:]
        return []
