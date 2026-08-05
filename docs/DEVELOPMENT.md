# AMM 开发文档

## 项目结构

```
amm/
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── openai_bridge.py      # OpenAI 兼容 API 桥接
│   │   └── diffusers_bridge.py   # Diffusers 文生图/视频桥接
│   ├── core/
│   │   ├── __init__.py
│   │   ├── engine.py             # 引擎抽象基类 + EngineRegistry
│   │   ├── model_manager.py      # 模型生命周期管理
│   │   └── version_manager.py    # 引擎版本安装/卸载
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── llama_cpp.py          # llama.cpp 引擎
│   │   ├── vllm.py               # vLLM 引擎
│   │   └── diffusers.py          # Diffusers 引擎
│   ├── config/
│   │   └── models_config.yaml    # 模型配置
│   └── server.py                 # aiohttp 主服务
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── deploy/
│   ├── install.sh                # 主安装脚本
│   ├── docker-compose.yml
│   ├── container_start.sh
│   └── scripts/                  # 各引擎安装脚本
└── docs/
    └── DEVELOPMENT.md
```

## 核心概念

### EngineRegistry

引擎注册中心，管理所有可用的推理引擎实现：

```python
from backend.core.engine import EngineRegistry
from backend.engines import LlamaCppEngine, VllmEngine, DiffusersEngine

registry = EngineRegistry()
registry.register(LlamaCppEngine())
registry.register(VllmEngine())
registry.register(DiffusersEngine())

# 获取推荐引擎
engine = registry.get_recommended("chat")
```

### ModelManager

统一管理模型实例的生命周期：

```python
from backend.core.model_manager import ModelManager

manager = ModelManager("/amm/backend/config/models_config.yaml")

# 启动模型
result = await manager.start_model("chat")

# 切换引擎
manager.update_engine("chat", "vllm")

# 更新参数
manager.update_parameters("chat", {"temp": 0.8})
```

### BaseEngine

所有引擎必须实现的抽象接口：

```python
from backend.core.engine import BaseEngine

class MyEngine(BaseEngine):
    engine_type = "my_engine"

    def get_display_name(self) -> str: ...
    def get_supported_categories(self) -> List[str]: ...
    def get_description(self) -> str: ...
    async def build_command(self, model_cfg, inst, models_dir, host) -> List[str]: ...
    async def validate_model(self, model_cfg, models_dir) -> Dict: ...
    async def health_check(self, inst) -> Dict: ...
```

## 添加新模型

编辑 `backend/config/models_config.yaml`：

```yaml
my_model:
  id: "my_id"
  name: "My Model"
  category: "chat"
  port: 18090
  engine_type: "llama_cpp"
  available_engines: ["llama_cpp", "vllm"]
  available_models:
    - name: "My-GGUF"
      file: "subdir/my-model-Q4_K_M.gguf"
  parameters:
    - name: "temp"
      label: "温度"
      type: "float"
      default: 0.7
```

然后在 `backend/core/model_manager.py` 的 `_model_keys()` 中添加 `"my_model"`。

## API 扩展

在 `backend/server.py` 的路由区添加新接口：

```python
app.router.add_get("/api/my/endpoint", my_handler)
```

或通过桥接模块注册：

```python
from backend.api.my_bridge import setup_routes
setup_routes(app, manager)
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PYTHONPATH` | Python 包搜索路径 | `/amm` |
| `AMM_ROOT` | 项目根目录 | `/amm` |
| `MODELS_DIR` | 模型文件目录 | `/models` |
| `AMM_ENGINES_ROOT` | 引擎安装目录 | `/amm/backend/engines_installed` |
| `CUDA_HOME` | CUDA 安装路径 | `/usr/local/cuda` |
| `HF_HOME` | HuggingFace 缓存 | `/models/huggingface` |
| `MODELSCOPE_CACHE` | ModelScope 缓存 | `/models/modelscope` |

## 调试

```bash
# 本地测试（不进入容器）
cd /amm
PYTHONPATH=/amm python3.11 -c "from backend.core.model_manager import ModelManager; m = ModelManager('backend/config/models_config.yaml'); print(len(m.instances))"

# 容器内调试
export PYTHONPATH=/amm
export AMM_ROOT=/amm
python3.11 backend/server.py
```

## 推送到 Gitea

```bash
cd /amm
git add -A
git commit -m "your message"
git push origin master
```

## 已知问题

1. **llama.cpp .so 依赖**: 编译产物需要复制到 `/usr/local/lib` 并运行 `ldconfig`
2. **GPU 显存不足**: RTX 6000D 84GB 可用，但需注意并发模型加载
3. **Diffusers pipeline 首次加载慢**: 首次推理会自动下载模型权重
