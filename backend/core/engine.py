"""
AMM Engine Abstraction Layer
=============================
混合推理引擎架构的核心抽象。支持 vllm / llama.cpp / diffusers 三种引擎类型，
每种引擎可以有多个版本并行安装。
"""
import asyncio
import logging
import os
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("AMM.Engine")


@dataclass
class EngineVersion:
    """引擎版本信息"""
    engine_type: str          # "vllm" | "llama_cpp" | "diffusers"
    version: str              # 版本号
    install_path: str         # 安装路径
    binary_path: Optional[str] = None  # 可执行文件路径
    is_default: bool = False
    status: str = "available" # available | installing | installed | error
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelInstance:
    """模型运行时实例"""
    model_id: str
    name: str
    category: str
    port: int
    engine_type: str = ""       # 当前使用的引擎类型
    engine_version: str = ""    # 当前使用的引擎版本
    status: str = "stopped"     # stopped | starting | running | error
    pid: Optional[int] = None
    process: Optional[Any] = None
    gpu_memory_mb: float = 0.0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    uptime_seconds: float = 0.0
    start_time: Optional[float] = None
    selected_model_file: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    log_lines: List[str] = field(default_factory=list)
    request_count: int = 0
    error_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（排除不可 pickle 的 process 字段，避免 asdict deepcopy 崩溃）"""
        return {
            "model_id": self.model_id,
            "name": self.name,
            "category": self.category,
            "port": self.port,
            "engine_type": self.engine_type,
            "engine_version": self.engine_version,
            "status": self.status,
            "pid": self.pid,
            "gpu_memory_mb": self.gpu_memory_mb,
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "uptime_seconds": self.uptime_seconds,
            "start_time": self.start_time,
            "selected_model_file": self.selected_model_file,
            "parameters": self.parameters,
            "log_lines": self.log_lines,
            "request_count": self.request_count,
            "error_count": self.error_count,
        }


class BaseEngine(ABC):
    """
    推理引擎基类。每种引擎 (vllm, llama.cpp, diffusers) 需实现此接口。
    """

    engine_type: str = "base"

    def __init__(self, engine_dir: str = ""):
        if not engine_dir:
            # Default: same dir as this file's project or /amm
            import os as _os
            base = _os.environ.get("AMM_ROOT", "/amm")
            engine_dir = f"{base}/backend/engines_installed"
        self.engine_dir = Path(engine_dir) / self.engine_type
        self.engine_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def get_display_name(self) -> str:
        """引擎的显示名称"""
        ...

    @abstractmethod
    def get_supported_categories(self) -> List[str]:
        """该引擎支持的模型类别列表: chat|embedding|asr|tts|reranker|ocr|image|video"""
        ...

    @abstractmethod
    def get_description(self) -> str:
        """引擎的功能描述"""
        ...

    @abstractmethod
    async def list_installed_versions(self) -> List[EngineVersion]:
        """列出当前已安装的所有版本"""
        ...

    @abstractmethod
    async def get_available_versions(self) -> List[EngineVersion]:
        """列出可安装的版本（在线查询或预设）"""
        ...

    @abstractmethod
    async def build_command(self, model_cfg: Dict, inst: ModelInstance, models_dir: str, host: str) -> List[str]:
        """根据模型配置和实例参数构建启动命令行"""
        ...

    @abstractmethod
    async def validate_model(self, model_cfg: Dict, models_dir: str) -> Dict[str, Any]:
        """验证模型文件和配置是否就绪，返回 {ok: bool, error: str}"""
        ...

    # ---- 进程管理 (共用实现，子类可 override) ----

    async def start_process(self, cmd: List[str], log_path: str) -> asyncio.subprocess.Process:
        """启动引擎子进程"""
        log_dir = os.path.dirname(log_path)
        os.makedirs(log_dir, exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as log_file:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
        logger.info(f"[{self.engine_type}] 进程启动 PID={process.pid}: {' '.join(cmd[:5])}...")
        return process

    def _descendant_pids(self, root_pid: int) -> List[int]:
        """递归收集 root_pid 的所有后代 PID（含脱离原进程组的子进程）。

        背景：vLLM 的 EngineCore / ray worker 等子进程可能调用 setsid
        脱离父进程组，仅用 killpg 会遗留子进程。这里通过 /proc/<pid>/stat
        的 PPID 关系遍历整棵进程树。
        """
        try:
            import psutil
        except Exception:
            psutil = None

        pids: List[int] = []
        try:
            if psutil is not None:
                try:
                    parent = psutil.Process(root_pid)
                except Exception:
                    return []
                stack = list(parent.children(recursive=True))
                while stack:
                    child = stack.pop()
                    try:
                        if child.is_running():
                            pids.append(child.pid)
                            stack.extend(child.children(recursive=False))
                    except Exception:
                        continue
                return pids
        except Exception as e:
            logger.warning(f"[{self.engine_type}] psutil 遍历进程树失败，回退 /proc: {e}")

        # 兜底：用 /proc 手工解析 PPID 关系（无 psutil 环境）
        try:
            children_map: Dict[int, List[int]] = {}
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/stat") as f:
                        parts = f.read().split()
                    ppid = int(parts[3])
                    children_map.setdefault(ppid, []).append(int(entry))
                except Exception:
                    continue
            stack = list(children_map.get(root_pid, []))
            seen = set()
            while stack:
                pid = stack.pop()
                if pid in seen:
                    continue
                seen.add(pid)
                pids.append(pid)
                stack.extend(children_map.get(pid, []))
            return pids
        except Exception as e:
            logger.warning(f"[{self.engine_type}] /proc 过程树遍历失败: {e}")
            return []

    async def stop_process(self, inst: ModelInstance):
        """停止引擎子进程（递归清理整棵进程树，含脱离进程组的 vLLM EngineCore）"""
        if not inst.process:
            return
        root_pid = inst.process.pid
        try:
            # 收集整棵进程树（含脱离子进程）
            tree = self._descendant_pids(root_pid)

            # 1) 先 SIGTERM 整棵进程树（从叶到根）
            for pid in reversed(tree):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                os.killpg(os.getpgid(root_pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

            # 2) 等待优雅退出
            await asyncio.sleep(2)

            # 3) 仍有存活则 SIGKILL 兜底
            surviving = [pid for pid in tree
                         if self._pid_alive(pid)]
            if inst.process.returncode is None:
                surviving.append(root_pid)
            for pid in surviving:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                if inst.process.returncode is None:
                    os.killpg(os.getpgid(root_pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

            # 4) 回收 root，避免僵尸
            try:
                inst.process.kill()
            except Exception:
                pass
            try:
                await inst.process.wait()
            except Exception:
                pass

            logger.info(f"[{self.engine_type}] 进程树已停止 root={root_pid}, 回收节点={len(tree)+1} (含{len(surviving)}个SIGKILL)")
        except Exception as e:
            logger.warning(f"[{self.engine_type}] 停止进程异常: {e}")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """检查 pid 是否存活（/proc 方式，无 psutil 依赖）"""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def is_process_alive(self, inst: ModelInstance) -> bool:
        """检查进程是否存活"""
        if not inst.process:
            return False
        return inst.process.returncode is None

    # ---- 健康检查 ----

    @abstractmethod
    async def health_check(self, inst: ModelInstance) -> Dict[str, Any]:
        """对运行的模型实例进行健康检查"""
        ...


# ============================================================
# Engine Registry
# ============================================================
class EngineRegistry:
    """引擎注册中心，管理所有可用的引擎实现"""

    def __init__(self):
        self._engines: Dict[str, BaseEngine] = {}

    def register(self, engine: BaseEngine):
        """注册引擎"""
        self._engines[engine.engine_type] = engine
        logger.info(f"引擎已注册: {engine.engine_type} ({engine.get_display_name()})")

    def get(self, engine_type: str) -> Optional[BaseEngine]:
        """获取引擎实例"""
        return self._engines.get(engine_type)

    def list_all(self) -> Dict[str, BaseEngine]:
        """列出所有引擎"""
        return self._engines

    def list_for_category(self, category: str) -> Dict[str, BaseEngine]:
        """获取支持某类模型的引擎列表"""
        return {
            k: v for k, v in self._engines.items()
            if category in v.get_supported_categories()
        }

    def get_recommended(self, category: str) -> Optional[BaseEngine]:
        """获取推荐引擎（首个支持的引擎）"""
        engines = self.list_for_category(category)
        # 优先 llama_cpp 用于 GGUF, diffusers 用于 image/video
        if category in ("image", "video"):
            return engines.get("diffusers") or list(engines.values())[0] if engines else None
        return engines.get("llama_cpp") or engines.get("vllm") or list(engines.values())[0] if engines else None
