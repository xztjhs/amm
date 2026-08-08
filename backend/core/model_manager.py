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
from backend.engines import LlamaCppEngine, VllmEngine, DiffusersEngine

logger = logging.getLogger("AMM.Manager")

# 部署路径
AMM_ROOT = os.environ.get("AMM_ROOT", "/amm")
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
LOGS_DIR = os.path.join(AMM_ROOT, "logs")


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

        # 初始化版本管理器
        self.version_manager = VersionManager(self.registry)

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
        """从磁盘恢复参数、模型文件和引擎选择"""
        state_file = Path(LOGS_DIR) / "state.json"
        if not state_file.exists():
            return
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
                    if saved.get("startup_command") is not None:
                        self.instances[model_id].startup_command = saved["startup_command"]
            logger.info(f"状态已恢复: {len(state.get('instances', {}))} 个模型")
        except Exception as e:
            logger.warning(f"恢复状态失败: {e}")

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
                    "startup_command": inst.startup_command,
                }
            state_file = Path(LOGS_DIR) / "state.json"
            os.makedirs(LOGS_DIR, exist_ok=True)
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
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
                    "status": v.status,
                    "install_path": v.install_path,
                    "binary_path": v.binary_path,
                    "is_default": v.is_default,
                    "metadata": v.metadata or {},
                })
        return result

    async def install_engine_version(self, engine_type: str, version: str):
        """安装引擎版本"""
        task = await self.version_manager.install(engine_type, version)
        return {
            "engine_type": task.engine_type,
            "version": task.version,
            "status": task.status,
            "message": task.message,
        }

    async def uninstall_engine_version(self, engine_type: str, version: str):
        """卸载引擎版本"""
        return await self.version_manager.uninstall(engine_type, version)

    def get_install_task(self, engine_type: str, version: str):
        """获取安装任务进度"""
        return self.version_manager.get_install_task(engine_type, version)

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
    ADVANCED_FIELDS = {"quant", "compute_dtype", "boundary_ratio", "cpu_offload"}

    def update_advanced_settings(self, model_id: str, settings: Dict[str, Any]):
        """更新 Diffusers 引擎的高级设置 (FP8 量化 / CPU offload / boundary 等) 到 yaml

        支持字段: quant, compute_dtype, boundary_ratio, cpu_offload
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
                # 内置桥接模式 (diffusers)，标记为运行
                inst.pid = 0

            inst.status = "running"
            logger.info(f"模型 {model_id} 已启动, engine={inst.engine_type}, PID={inst.pid}")
            return {"success": True, "model_id": model_id, "status": inst.status, "pid": inst.pid, "engine": inst.engine_type}

        except Exception as e:
            inst.status = "error"
            logger.error(f"启动模型 {model_id} 失败: {e}")
            return {"error": str(e)}

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
            return {"success": True, "model_id": model_id, "status": "stopped"}

        except Exception as e:
            logger.error(f"停止模型 {model_id} 失败: {e}")
            return {"error": str(e)}

    async def restart_model(self, model_id: str):
        await self.stop_model(model_id)
        await asyncio.sleep(2)
        return await self.start_model(model_id)

    def _find_model_config(self, model_id: str) -> Optional[Dict]:
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
            "uptime_seconds": time.time() - psutil.boot_time(),
        }

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
