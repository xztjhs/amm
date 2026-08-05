"""
AMM Version Manager
===================
管理多个推理引擎版本的安装、卸载、切换。
支持 vllm / llama.cpp / diffusers 各版本并行共存。
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import venv
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from backend.core.engine import EngineRegistry, EngineVersion, BaseEngine

logger = logging.getLogger("AMM.VersionManager")

# 引擎安装根目录
ENGINES_ROOT = os.environ.get("AMM_ENGINES_ROOT", os.environ.get("AMM_ROOT", "/amm") + "/backend/engines_installed")


@dataclass
class InstallTask:
    """安装任务状态"""
    engine_type: str
    version: str
    status: str = "pending"  # pending | downloading | building | installing | done | failed
    progress: float = 0.0
    message: str = ""
    log: List[str] = field(default_factory=list)


class VersionManager:
    """引擎版本管理器"""

    def __init__(self, registry: EngineRegistry):
        self.registry = registry
        self.root = Path(ENGINES_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self._running_tasks: Dict[str, InstallTask] = {}

    def _task_key(self, engine_type: str, version: str) -> str:
        return f"{engine_type}/{version}"

    async def list_all_versions(self) -> Dict[str, List[EngineVersion]]:
        """列出所有引擎的所有版本"""
        result = {}
        # 安装过版本的记录
        installed_db = self._load_installed_db()

        for engine_type, engine in self.registry.list_all().items():
            all_versions = await engine.get_available_versions()
            for v in all_versions:
                key = f"{engine_type}_{v.version}"
                if key in installed_db:
                    v.status = "installed"
                    v.install_path = installed_db[key].get("install_path", v.install_path)
            result[engine_type] = all_versions
        return result

    async def install(self, engine_type: str, version: str) -> InstallTask:
        """安装指定引擎版本的"""
        engine = self.registry.get(engine_type)
        if not engine:
            raise ValueError(f"未知引擎: {engine_type}")

        task = InstallTask(engine_type=engine_type, version=version, status="installing")
        task_key = self._task_key(engine_type, version)
        self._running_tasks[task_key] = task

        try:
            install_path = self.root / engine_type / version
            install_path.mkdir(parents=True, exist_ok=True)

            if engine_type == "llama_cpp":
                await self._install_llama_cpp(task, install_path)
            elif engine_type == "vllm":
                await self._install_vllm(task, install_path)
            elif engine_type == "diffusers":
                await self._install_diffusers(task, install_path)
            else:
                raise ValueError(f"不支持的引擎安装: {engine_type}")

            task.status = "done"
            task.message = f"{engine_type} {version} 安装完成"
            self._save_installed(engine_type, version, str(install_path))

        except Exception as e:
            task.status = "failed"
            task.message = str(e)
            logger.error(f"安装失败 [{engine_type}/{version}]: {e}")

        return task

    async def uninstall(self, engine_type: str, version: str) -> Dict[str, Any]:
        """卸载指定引擎版本"""
        install_path = self.root / engine_type / version
        if install_path.exists():
            shutil.rmtree(install_path)
        self._remove_installed(engine_type, version)
        logger.info(f"已卸载: {engine_type}/{version}")
        return {"ok": True, "message": f"{engine_type} {version} 已卸载"}

    async def _install_llama_cpp(self, task: InstallTask, install_path: Path):
        """安装 llama.cpp (从源码编译)"""
        bin_dir = install_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        task.message = "克隆源码..."
        clone_dir = install_path / "src"

        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1",
            "https://github.com/ggerganov/llama.cpp.git",
            str(clone_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone 失败: {stderr.decode()}")

        task.message = "编译 llama.cpp (CUDA)..."
        build_dir = clone_dir / "build"
        build_dir.mkdir(exist_ok=True)

        # cmake
        proc = await asyncio.create_subprocess_exec(
            "cmake", "-S", str(clone_dir), "-B", str(build_dir),
            "-DGGML_CUDA=ON",
            "-DCMAKE_CUDA_ARCHITECTURES=native",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"cmake 失败: {stderr.decode()}")

        # build
        proc = await asyncio.create_subprocess_exec(
            "cmake", "--build", str(build_dir), "--config", "Release",
            "-j", str(os.cpu_count() or 4),
            "--target", "llama-server",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"编译失败: {stderr.decode()}")

        # 复制产物
        shutil.copy2(build_dir / "bin" / "llama-server", bin_dir / "llama-server")
        task.message = "llama.cpp 编译安装完成"

    async def _install_vllm(self, task: InstallTask, install_path: Path):
        """安装 vLLM (使用 pip 虚拟环境)"""
        venv_path = install_path / "venv"
        bin_dir = install_path / "bin"

        task.message = "创建虚拟环境..."
        venv.create(venv_path, with_pip=True)
        pip = str(venv_path / "bin" / "pip")
        python = str(venv_path / "bin" / "python")

        task.message = "安装 vLLM..."
        proc = await asyncio.create_subprocess_exec(
            pip, "install",
            f"vllm=={task.version}",
            "-i", "https://mirrors.aliyun.com/pypi/simple/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"vLLM 安装失败: {stderr.decode()[-500:]}")

        # 创建符号链接
        bin_dir.mkdir(parents=True, exist_ok=True)
        src = venv_path / "bin" / "vllm"
        dst = bin_dir / "vllm"
        if not dst.exists():
            dst.symlink_to(src)

        task.message = "vLLM 安装完成"

    async def _install_diffusers(self, task: InstallTask, install_path: Path):
        """安装 Diffusers (使用 pip 虚拟环境)"""
        venv_path = install_path / "venv"
        bin_dir = install_path / "bin"

        task.message = "创建虚拟环境..."
        venv.create(venv_path, with_pip=True)
        pip = str(venv_path / "bin" / "pip")

        # 先装 PyTorch
        task.message = "安装 PyTorch (CUDA)..."
        proc = await asyncio.create_subprocess_exec(
            pip, "install", "torch", "torchvision",
            "--index-url", "https://download.pytorch.org/whl/cu121",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"PyTorch 安装失败: {stderr.decode()[-500:]}")

        # 安装 diffusers 相关
        task.message = "安装 diffusers + transformers..."
        packages = [
            f"diffusers=={task.version}",
            "transformers>=4.49.0",
            "accelerate>=1.0.0",
            "safetensors>=0.4.0",
            "peft>=0.12.0",
            "modelscope>=1.21.0",
            "open_clip_torch",
            "imageio",
            "imageio-ffmpeg",
        ]
        proc = await asyncio.create_subprocess_exec(
            pip, "install", *packages,
            "-i", "https://mirrors.aliyun.com/pypi/simple/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Diffusers 安装失败: {stderr.decode()[-500:]}")

        task.message = "Diffusers 安装完成"

    # ---- 已安装记录持久化 ----

    def _installed_db_path(self) -> Path:
        return self.root / "installed.json"

    def _load_installed_db(self) -> Dict:
        db_path = self._installed_db_path()
        if db_path.exists():
            try:
                with open(db_path, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_installed(self, engine_type: str, version: str, install_path: str):
        db = self._load_installed_db()
        key = f"{engine_type}_{version}"
        db[key] = {
            "engine_type": engine_type,
            "version": version,
            "install_path": install_path,
        }
        with open(self._installed_db_path(), 'w') as f:
            json.dump(db, f, indent=2)

    def _remove_installed(self, engine_type: str, version: str):
        db = self._load_installed_db()
        key = f"{engine_type}_{version}"
        db.pop(key, None)
        with open(self._installed_db_path(), 'w') as f:
            json.dump(db, f, indent=2)

    def get_install_task(self, engine_type: str, version: str) -> Optional[InstallTask]:
        """获取安装任务状态"""
        return self._running_tasks.get(self._task_key(engine_type, version))
