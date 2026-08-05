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

## 部署验证环境（开发/测试机）

> **平台**: 10.10.10.10 容器（Unraid 宿主）
> **SSH**: `ssh -p 62022 root@10.10.10.10`（密码 CHANGE_ME_PASSWORD）
> **GPU**: NVIDIA RTX 6000D ×1（84 GB 显存）
> **Python**: 3.11（`/usr/bin/python3.11`），系统默认 python3 为 3.9
> **模型目录**: `/models`（7TB 共享盘，已含 HauhauCS/Qwen/ggml-org/mradermacher 等 GGUF）
> **Web UI**: `http://10.10.10.10:60006/`

### ⚠️ 显存管理铁律（务必遵守）

**每类模型部署、测试、验证时，只开启 1 个模型实例，依次串行进行，严禁并发多模型同时加载**，避免 84GB 显存不够用导致 OOM 崩掉已运行的模型。

推荐验证顺序（由轻到重、按当前就绪度）：

1. **Chat / LLM**（llama_cpp，引擎已装 b4727）
2. **Embedding**（llama_cpp）
3. **ASR / TTS / Reranker / OCR**（llama_cpp）
4. **Text-to-Image**（Diffusers，需先装引擎 + 拉取 Qwen-Image-2512）
5. **Text-to-Video / Image-to-Video**（Diffusers，需 28GB 级模型，最重，最后验证）
6. **vLLM**（目前引擎目录为空，需先 `install_engine_version` 安装）

### 当前就绪度快照（2026-08-05）

- [x] llama_cpp 引擎 b4727（llama-server 已编译）
- [x] **llama-tts 工具已编译**（方案1: TTS 通过 llama-tts CLI 按需生成，非 server 常驻）
- [x] **vLLM 0.22.1（CUDA 13 + Blackwell 验证通过）**：
  - venv: `/amm/backend/engines_installed/vllm/0.22.1`（torch 2.11.0+cu130，arch 含 sm_120）
  - 引擎优先使用 0.22.1（0.8.5 为 cu124 不支持 Blackwell，保留作历史）；0.22.1 在 `VLLM_VERSIONS` 列表首位并 is_default=True
  - chat 实例已切 vllm/0.22.1 + `/models/Qwen3-4B`（safetensors），外部 60008 全链路推理验证通过
  - 安装：`--index-url aliyun --extra-index-url https://download.pytorch.org/whl/cu130`（torch 走 cu130 源）
  - ⚠️ vLLM 需 HF safetensors 模型（GGUF 的 qwen35moe 不支持）；gpu_memory_utilization 需调低至 ~0.65（Baseline 显存占用）
- [x] Diffusers 引擎（目录空，待安装验证）
- [x] AMM server 运行中（端口 8080，宿主映射 60008）

### 已知问题（2026-08-05 补充）

- **AMM stop/restart 模型进程残留 bug**：stop 模型时旧 vllm/llama-server 进程（含 VLLM::EngineCore 子进程）有时不退出，导致端口 18081 被占、新实例 error。临时解决：手动 `kill -9` 残留（`ps aux | grep -E "openai.api_server|VLLM::EngineCore"`），显存随之释放。
- **OpenAI bridge model 名映射**：vLLM 端 served model 是模型路径（如 `/models/Qwen3-4B`），AMM bridge 需把 AMM 内部 id（chat）重写为路径（`_rewrite_vllm_model`）。`/v1/chat/completions` 路由实际指向 `chat_completions_stream`（含重写），非流式走该 handler 也可。

### 项目规则（老板 2026-08-05）

1. **模型资源**: 尽可能用 ModelScope 替代 Hugging Face
2. **软件源**: Python 等尽量用国内镜像（阿里云、清华 TUNA 等）
3. **推理引擎优先级**: vllm > llama.cpp > Diffusers
4. **⚠️ CUDA 版本铁律（老板 2026-08-05 新增）**: 本机 CUDA 版本为 **13.2**（`/usr/local/cuda-13.2`，nvcc V13.2.86），**严禁使用 cu12 系列**的 PyTorch/依赖。所有需要 CUDA 的推理引擎（vLLM、Diffusers、含 torch）必须安装支持 CUDA 13 或 Blackwell sm_120 的版本：
   - PyTorch 必须使用 cu13 构建（如 `+cu130`），**禁止 cu124/cu126/cu128**
   - vLLM 需升级到支持 Blackwell (sm_120 / compute capability 12.0) 的版本（vLLM 0.8.5 锁 torch==2.6.0+cu124，不兼容 CUDA 13，**不可用**）
   - GPU 为 RTX 6000D，compute capability **12.0 (sm_120)**
   - 判断依据：`torch.version.cuda` 必须 >= 13，`nvidia-smi --query-gpu=compute_cap` 应为 12.0

### TTS 集成说明（llama-tts）

- AI 引擎支持三种 audio 端点：`/v1/audio/transcriptions`(ASR)、`/v1/audio/speech`(TTS)、`/v1/ocr`(OCR)
- **TTS 走 llama-tts CLI 按需生成**（非常驻 server）：`/v1/audio/speech` 端点读取 tts 实例的模型+mmproj，调用 `/usr/local/bin/llama-tts` 生成 wav 后返回
- TTS 模型: `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF`（ModelScope 下载，backbone Q4_K_M + mmproj bf16）
- llama-tts 需要 backbone + audio mmproj 两个文件（旧 Multilingual-TTS 缺 mmproj，不可用）

### 排查/调试入口

- 服务日志: `/amm/logs/amm_server.log`
- 引擎安装目录: `/amm/backend/engines_installed/`
- GPU 状态: `nvidia-smi`（当前基线显存占用需先核对外部任务）

## 已知问题

1. **llama.cpp .so 依赖**: 编译产物需要复制到 `/usr/local/lib` 并运行 `ldconfig`
2. **GPU 显存不足**: RTX 6000D 84GB 可用，但需注意并发模型加载
3. **Diffusers pipeline 首次加载慢**: 首次推理会自动下载模型权重
