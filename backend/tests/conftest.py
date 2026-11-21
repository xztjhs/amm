"""
AMM 回归测试环境准备
=====================
关键约束: backend.server / backend.core.model_manager 在 **import 时** 读取
AMM_ROOT / MODELS_DIR 环境变量决定配置路径与日志路径，因此必须在导入
backend 包之前完成环境变量注入，否则会写到真实 /amm 与仓库配置。

这里使用独立的临时沙箱目录，绝不触碰:
  - 仓库里的 backend/config/models_config.yaml
  - 部署环境的 /models、/amm/logs

用例隔离:
  多个 AioHTTPTestCase 共享同一沙箱（导入时确定的 AMM_ROOT）。
  若不清洗，前一个用例写入的 logs/state.json（引擎选择/flavor）、
  data/amm.db（动态实例）、engines/（伪造安装目录）会污染后续用例，
  曾导致「单跑通过、全量失败」。因此每个用例开始前统一重置运行期状态。
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# 仓库根目录: backend/tests/conftest.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]

# 沙箱目录（进程级唯一，import 时即生效）
_SANDBOX = tempfile.mkdtemp(prefix="amm_regression_")
_SANDBOX_CFG_DIR = Path(_SANDBOX) / "backend" / "config"
_SANDBOX_CFG_DIR.mkdir(parents=True, exist_ok=True)
_SANDBOX_MODELS = Path(_SANDBOX) / "models"
_SANDBOX_MODELS.mkdir(parents=True, exist_ok=True)

# 真实配置副本（每个用例都会被重置回这份基线）
_SRC_CFG = REPO_ROOT / "backend" / "config" / "models_config.yaml"
_CFG_TARGET = _SANDBOX_CFG_DIR / "models_config.yaml"
_PRISTINE_CFG = Path(_SANDBOX) / ".models_config.baseline.yaml"
if _SRC_CFG.exists():
    shutil.copy2(_SRC_CFG, _CFG_TARGET)
    shutil.copy2(_SRC_CFG, _PRISTINE_CFG)

# 伪造模型文件（fs / discover 端点需要）
for _fake in ("fake-chat.gguf", "fake-embedding.gguf"):
    (_SANDBOX_MODELS / _fake).write_text("dummy", encoding="utf-8")

# 内置模版库：复制仓库 templates/ 到沙箱（模版接口从 AMM_ROOT/templates 读取）
_SRC_TEMPLATES = REPO_ROOT / "templates"
_TEMPLATES_TARGET = Path(_SANDBOX) / "templates"
if _SRC_TEMPLATES.is_dir():
    shutil.copytree(_SRC_TEMPLATES, _TEMPLATES_TARGET, dirs_exist_ok=True)

# 环境变量必须在 import backend.* 之前设置
os.environ["AMM_ROOT"] = _SANDBOX
os.environ["MODELS_DIR"] = str(_SANDBOX_MODELS)
os.environ["AMM_ENGINES_ROOT"] = str(Path(_SANDBOX) / "engines")
os.environ["AMM_START_PRELOAD"] = "0"


def _reset_runtime_state() -> None:
    """重置所有运行期可变状态，保证用例之间零耦合"""
    # 1) 参数/引擎选择状态
    state = Path(_SANDBOX) / "logs" / "state.json"
    try:
        state.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    # 2) 动态实例 SQLite
    db = Path(_SANDBOX) / "data" / "amm.db"
    try:
        db.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    # 3) 测试里伪造的引擎安装目录
    shutil.rmtree(Path(_SANDBOX) / "engines", ignore_errors=True)

    # 3b) 模版库恢复为仓库基线（用例可能保存/删除模版）
    if _SRC_TEMPLATES.is_dir():
        shutil.rmtree(_TEMPLATES_TARGET, ignore_errors=True)
        try:
            shutil.copytree(_SRC_TEMPLATES, _TEMPLATES_TARGET, dirs_exist_ok=True)
        except OSError:
            pass

    # 4) 高级设置会写回 yaml，恢复基线
    if _PRISTINE_CFG.exists():
        try:
            shutil.copy2(_PRISTINE_CFG, _CFG_TARGET)
        except OSError:
            pass


def pytest_runtest_setup(item):
    """每个用例执行前重置沙箱运行期状态（对 unittest.TestCase 同样生效）"""
    _reset_runtime_state()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_sandbox():
    """会话结束后清理沙箱"""
    yield
    shutil.rmtree(_SANDBOX, ignore_errors=True)


@pytest.fixture(scope="session")
def sandbox_root() -> Path:
    return Path(_SANDBOX)


@pytest.fixture(scope="session")
def sandbox_models_dir() -> Path:
    return _SANDBOX_MODELS
