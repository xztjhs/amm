# AMM — AI Models Manage

> 混合推理引擎架构下的 AI 模型统一管理与调度平台

[![Engine](https://img.shields.io/badge/engine-hybrid-blue)](https://github.com/ggerganov/llama.cpp)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 特性

- **混合推理引擎**: 支持 llama.cpp (GGUF) / vLLM / Diffusers 三种后端，可自由切换
- **9 类 AI 模型**: Chat / Embedding / ASR / TTS / Reranker / OCR / T2I / T2V / I2V
- **OpenAI 兼容 API**: `/v1/chat/completions`, `/v1/embeddings`, `/v1/images/generations`, `/v1/videos/generations`
- **FP8 量化 (Diffusers)**: Wan2.2-A14B MoE 自动启用 FP8 存储+BF16 计算，84G 显存可跑 27B 视频模型
- **网页端高级配置**: Quant / CPU Offload / Boundary Ratio / Compute Dtype 一键切换
- **浅色主题**: 🌙/☀️ 一键切换，localStorage 持久记忆
- **引擎版本管理**: 网页端安装/卸载不同版本引擎
- **实时监控**: GPU / CPU / 内存 / 磁盘监控，模型运行状态实时刷新

## 架构

```
┌─────────────────────────────────────────────┐
│            AMM Web UI (Frontend)             │
│   Dashboard │ Models │ GPU │ Logs │ Settings │
└─────────────┬───────────────────────────────┘
              │ REST API
┌─────────────▼───────────────────────────────┐
│          AMM Backend Server                  │
│  ┌──────────┬──────────┬──────────────────┐ │
│  │ Engine   │ Version  │ Model Manager    │ │
│  │ Registry │ Manager  │ (Lifecycle)      │ │
│  └────┬─────┴────┬─────┴────────┬─────────┘ │
│       │          │              │           │
│  ┌────▼────┐ ┌──▼───┐    ┌─────▼──────┐   │
│  │llama.cpp│ │vLLM  │    │Diffusers   │   │
│  │ (GGUF)  │ │(safet│    │(ModelScope)│   │
│  └─────────┘ └──────┘    └────────────┘   │
└─────────────────────────────────────────────┘
```

## 快速开始

### 1. 环境要求

- Linux (Rocky Linux 9 / Ubuntu 22.04 / Debian 12)
- Python 3.11+
- NVIDIA GPU（推荐 RTX 6000D / A100 / H100，显存 ≥48G）
- CUDA 13.2+（需支持 Blackwell sm_120，本项目使用 torch 2.11.0+cu130）

### 2. 安装

```bash
# 克隆仓库
git clone http://192.168.100.245:60005/iei/amm.git /amm
cd /amm

# 运行安装脚本 (容器内首次使用)
bash deploy/install.sh
```

### 3. 启动服务

```bash
# 方式1: 手动启动
export PYTHONPATH=/amm
export AMM_ROOT=/amm
export MODELS_DIR=/models
python3.11 /amm/backend/server.py

# 方式2: 使用容器启动脚本
bash deploy/container_start.sh
```

### 4. 访问 Web 界面

打开浏览器访问: `http://<服务器IP>:18080/`

### 5. Docker Compose 部署

```bash
cd deploy
docker compose up -d
# 进入容器执行安装
docker exec -it amm-server bash
bash /amm/deploy/install.sh
python3.11 /amm/backend/server.py
```

## 目录结构

```
/amm/
├── backend/
│   ├── api/               # API 桥接 (OpenAI / Diffusers)
│   ├── core/              # 核心引擎抽象 / 模型管理
│   ├── engines/           # 引擎实现 (llama_cpp / vllm / diffusers)
│   ├── config/            # models_config.yaml
│   └── server.py          # 主服务入口
├── frontend/              # Web UI
├── deploy/                # 部署脚本
├── logs/                  # 运行日志
└── docs/                  # 文档
```

## API 接口

### 系统接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/system` | GET | 系统信息 |
| `/api/gpu` | GET | GPU 状态 |
| `/api/instances` | GET | 所有模型实例 |
| `/api/engines` | GET | 引擎列表 |
| `/api/engines/versions` | GET | 引擎版本 |

### 模型管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/instances/{id}/start` | POST | 启动模型 |
| `/api/instances/{id}/stop` | POST | 停止模型 |
| `/api/instances/{id}/restart` | POST | 重启模型 |
| `/api/instances/{id}/parameters` | PUT | 更新运行参数 |
| `/api/instances/{id}/advanced` | GET/PUT | 读取/更新 Diffusers 高级配置（Quant/CPU Offload等） |
| `/api/instances/{id}/engine` | PUT | 切换引擎 |
| `/api/instances/{id}/logs` | GET | 查看日志 |

### OpenAI 兼容

| 接口 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 模型列表 |
| `/v1/chat/completions` | POST | 对话生成 |
| `/v1/embeddings` | POST | 文本嵌入 |
| `/v1/images/generations` | POST | 文生图 (T2I) |
| `/v1/videos/generations` | POST | 视频生成 (T2V/I2V, video_type=t2v\|i2v) |

## 引擎支持矩阵

| 模型类别 | llama.cpp | vLLM | Diffusers |
|----------|:---------:|:----:|:---------:|
| Chat / LLM | ✅ | ✅ | ❌ |
| Embedding | ✅ | ✅ | ❌ |
| ASR | ✅ | ❌ | ❌ |
| TTS | ✅ | ❌ | ❌ |
| Reranker | ✅ | ❌ | ❌ |
| OCR | ✅ | ❌ | ❌ |
| Text-to-Image | ❌ | ❌ | ✅ |
| Text-to-Video | ❌ | ❌ | ✅ |
| Image-to-Video | ❌ | ❌ | ✅ |

## 配置模型

编辑 `backend/config/models_config.yaml`：

```yaml
chat_model:
  id: "chat"
  engine_type: "llama_cpp"   # 或 "vllm"
  available_models:
    - name: "Qwen3.6-35B"
      file: "HauhauCS/.../Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
```

模型文件存放在 `/models/` 目录，路径相对于 `/models`。

## 开发

```bash
# 安装开发依赖
pip3.11 install -r backend/requirements.txt

# 运行测试
cd /amm
PYTHONPATH=/amm python3.11 -m backend.server
```

## 路线图

- [x] 混合引擎架构 (llama.cpp / vLLM / Diffusers)
- [x] 引擎版本管理 (安装/卸载)
- [x] 9 类模型统一配置
- [x] OpenAI 兼容 API (chat/embeddings/images/videos)
- [x] Web 前端 (Dashboard / Playground / GPU 监控 / Settings)
- [x] vLLM CUDA13 + Blackwell sm_120 验证 (0.22.1)
- [x] Diffusers FP8 layerwise_casting (Wan2.2 MoE)
- [x] Diffusers T2I 推理验证 (Qwen-Image-2512)
- [ ] Diffusers T2V/I2V 完全验证 (Wan2.2-A14B 下载中)
- [ ] 模型自动下载 (HuggingFace / ModelScope)
- [ ] 权限管理与多用户
- [ ] 模型量化转换工具 (GGUF)

## 许可证

MIT License
