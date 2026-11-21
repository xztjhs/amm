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


def _pip_index() -> str:
    """取 Settings 里配置的 PyPI 镜像（引擎安装与版本管理共用）"""
    try:
        from backend.core.engine_catalog import get_pip_index
        return get_pip_index()
    except Exception:
        return "https://mirrors.aliyun.com/pypi/simple/"

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
    flavor: str = ""          # cpu | cuda | "" (不区分构建变体)


class VersionManager:
    """引擎版本管理器"""

    def __init__(self, registry: EngineRegistry):
        self.registry = registry
        self.root = Path(ENGINES_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self._running_tasks: Dict[str, InstallTask] = {}

    @staticmethod
    def _dir_name(version: str, flavor: str = "") -> str:
        """版本目录名: {version} 或 {version}-{flavor}"""
        return f"{version}-{flavor}" if flavor else version

    def _task_key(self, engine_type: str, version: str, flavor: str = "") -> str:
        return f"{engine_type}/{self._dir_name(version, flavor)}"

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
                if v.flavor:
                    key2 = f"{engine_type}_{v.version}_{v.flavor}"
                    if key2 in installed_db and v.status != "installed":
                        v.status = "installed"
                        v.install_path = installed_db[key2].get("install_path", v.install_path)
            result[engine_type] = all_versions
        return result

    async def install(self, engine_type: str, version: str, flavor: str = "") -> InstallTask:
        """安装指定引擎版本（flavor: cpu | cuda | 空）"""
        engine = self.registry.get(engine_type)
        if not engine:
            raise ValueError(f"未知引擎: {engine_type}")

        task = InstallTask(engine_type=engine_type, version=version, status="installing", flavor=flavor)
        task_key = self._task_key(engine_type, version, flavor)
        self._running_tasks[task_key] = task

        try:
            install_path = self.root / engine_type / self._dir_name(version, flavor)
            install_path.mkdir(parents=True, exist_ok=True)

            if engine_type == "llama_cpp":
                await self._install_llama_cpp(task, install_path, flavor)
            elif engine_type == "sentence_transformers":
                await self._install_sentence_transformers(task, install_path, flavor)
            elif engine_type == "vllm":
                await self._install_vllm(task, install_path)
            elif engine_type == "diffusers":
                await self._install_diffusers(task, install_path)
            else:
                raise ValueError(f"不支持的引擎安装: {engine_type}")

            task.status = "done"
            task.message = f"{engine_type} {version} 安装完成"
            self._save_installed(engine_type, version, str(install_path), flavor=flavor)

        except Exception as e:
            task.status = "failed"
            task.message = str(e)
            logger.error(f"安装失败 [{engine_type}/{version}]: {e}")

        return task

    async def uninstall(self, engine_type: str, version: str, flavor: str = "") -> Dict[str, Any]:
        """卸载指定引擎版本"""
        install_path = self.root / engine_type / self._dir_name(version, flavor)
        if install_path.exists():
            shutil.rmtree(install_path)
        self._remove_installed(engine_type, version, flavor=flavor)
        logger.info(f"已卸载: {engine_type}/{self._dir_name(version, flavor)}")
        return {"ok": True, "message": f"{engine_type} {version}{'-' + flavor if flavor else ''} 已卸载"}

    async def _install_llama_cpp(self, task: InstallTask, install_path: Path, flavor: str = ""):
        """安装 llama.cpp (从源码编译)

        flavor=cpu  -> -DGGML_CUDA=OFF，纯 CPU 推理
        flavor=cuda -> -DGGML_CUDA=ON，基于容器内 CUDA 13.2
        空         -> 向后兼容，按 cuda 处理
        """
        use_cuda = flavor != "cpu"
        flavor = flavor or ("cuda" if use_cuda else "cpu")
        bin_dir = install_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        task.message = "拉取 llama.cpp 源码..."
        clone_dir = install_path / "src"
        if not (clone_dir / ".git").exists():
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1",
                "https://github.com/ggerganov/llama.cpp.git",
                str(clone_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"git clone 失败: {stderr.decode()[-500:]}")

        task.message = f"编译 llama.cpp ({flavor.upper()})..."
        build_dir = clone_dir / f"build-{flavor}"
        build_dir.mkdir(exist_ok=True)

        cmake_args = [
            "cmake", "-S", str(clone_dir), "-B", str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
        ]
        if use_cuda:
            cmake_args += ["-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=native"]
        else:
            cmake_args += ["-DGGML_CUDA=OFF"]

        proc = await asyncio.create_subprocess_exec(
            *cmake_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"cmake 失败: {stderr.decode()[-500:]}")

        proc = await asyncio.create_subprocess_exec(
            "cmake", "--build", str(build_dir), "--config", "Release",
            "-j", str(os.cpu_count() or 4),
            "--target", "llama-server",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"编译失败: {stderr.decode()[-500:]}")

        # 产物路径在 cuda/cpu 构建下均为 build/bin/llama-server
        src_bin = build_dir / "bin" / "llama-server"
        if not src_bin.exists():
            raise RuntimeError(f"编译产物缺失: {src_bin}")
        shutil.copy2(src_bin, bin_dir / "llama-server")
        os.chmod(bin_dir / "llama-server", 0o755)
        task.message = f"llama.cpp ({flavor}) 编译安装完成"

    async def _install_sentence_transformers(self, task: InstallTask, install_path: Path, flavor: str = ""):
        """安装 sentence_transformers（独立 venv）

        flavor=cpu  -> torch CPU wheel（省显存，GPU 被大模型占满时的备选）
        flavor=cuda -> torch cu128（PyTorch 无 cu130 wheel；cu128 可在 CUDA 13 驱动上运行）
        """
        flavor = flavor or "cuda"
        venv_path = install_path / "venv"
        bin_dir = install_path / "bin"

        task.message = "创建虚拟环境..."
        if not (venv_path / "bin" / "python").exists():
            venv.create(venv_path, with_pip=True)
        pip = str(venv_path / "bin" / "pip")

        if flavor == "cpu":
            task.message = "安装 PyTorch (CPU)..."
            torch_index = "https://download.pytorch.org/whl/cpu"
        else:
            task.message = "安装 PyTorch (CUDA 13.0)..."
            torch_index = os.environ.get("AMM_TORCH_CUDA_INDEX",
                                         "https://download.pytorch.org/whl/cu128")

        proc = await asyncio.create_subprocess_exec(
            pip, "install", "--upgrade", "pip",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # 精简镜像缺 cuBLAS/cuDNN，先补 CUDA 运行时（与 engine_catalog 保持同一组合）
        if flavor != "cpu":
            from backend.core.engine_catalog import TORCH_CUDA_RUNTIME_PKGS
            task.message = "安装 CUDA 运行时（cublas / cudnn / nccl …）..."
            proc = await asyncio.create_subprocess_exec(
                pip, "install", *TORCH_CUDA_RUNTIME_PKGS,
                "-i", _pip_index(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"CUDA 运行时安装失败: {stderr.decode()[-500:]}")

        # torch 优先走 PyPI 镜像（官方索引大文件易断），失败再回退官方索引
        torch_args = [pip, "install", "torch", "-i",
                      _pip_index()] if flavor != "cpu" \
            else [pip, "install", "torch", "--index-url", torch_index]
        proc = await asyncio.create_subprocess_exec(
            *torch_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 and flavor != "cpu":
            proc = await asyncio.create_subprocess_exec(
                pip, "install", "torch", "--index-url", torch_index,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"PyTorch 安装失败: {stderr.decode()[-500:]}")

        task.message = "安装 sentence-transformers..."
        proc = await asyncio.create_subprocess_exec(
            pip, "install",
            f"sentence-transformers=={task.version}",
            "transformers>=4.46.0",
            "huggingface-hub>=0.26.0",
            "-i", _pip_index(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"sentence-transformers 安装失败: {stderr.decode()[-500:]}")

        bin_dir.mkdir(parents=True, exist_ok=True)
        task.message = f"sentence_transformers ({flavor}) 安装完成"

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
            "-i", _pip_index(),
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
            "--index-url", os.environ.get("AMM_TORCH_CUDA_INDEX",
                                          "https://download.pytorch.org/whl/cu128"),
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
            "-i", _pip_index(),
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

    def _save_installed(self, engine_type: str, version: str, install_path: str, flavor: str = ""):
        db = self._load_installed_db()
        key = f"{engine_type}_{version}" + (f"_{flavor}" if flavor else "")
        db[key] = {
            "engine_type": engine_type,
            "version": version,
            "flavor": flavor,
            "install_path": install_path,
        }
        with open(self._installed_db_path(), 'w') as f:
            json.dump(db, f, indent=2)

    def _remove_installed(self, engine_type: str, version: str, flavor: str = ""):
        db = self._load_installed_db()
        key = f"{engine_type}_{version}" + (f"_{flavor}" if flavor else "")
        db.pop(key, None)
        with open(self._installed_db_path(), 'w') as f:
            json.dump(db, f, indent=2)

    def get_install_task(self, engine_type: str, version: str, flavor: str = "") -> Optional[InstallTask]:
        """获取安装任务状态"""
        return self._running_tasks.get(self._task_key(engine_type, version, flavor))
