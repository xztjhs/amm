# AMM：一套支持 llama.cpp / vLLM / Diffusers 的 AI 模型统一调度平台

> 原文为 CSDN 博客投稿稿，欢迎收藏、评论交流。

## 写在前面

本地部署过大模型的朋友应该都体会过这种"头痛"：

- Chat 模型要用 llama.cpp，Embedding 用同一套，ASR/TTS 又要单独起一个进程；
- 每来一个需求，就要手动开一个端口、配一个服务、记一堆地址；
- 想从 llama.cpp 换成 vLLM 提速，又得重新折腾一遍；
- 图片、视频生成类模型和文本模型完全是两套体系，很难统一管理。

需求多了以后，桌面和服务器上就会躺着一堆独立进程，互相之间没有任何统一入口。**AMM（AI Models Manage）** 就是为了解决这个问题而生的——它用一套统一 API 和 Web 管理界面，把文本、语音、图像、视频几大类模型拢到一起，底层引擎可以任意切换。

下面从设计、架构到部署，完整介绍一下这个项目。

---

## 一、它解决了什么问题？

一句话：**用一套平台统一管理、调度多类 AI 模型的推理服务**。

具体来说：

| 痛点 | AMM 的解法 |
|------|-----------|
| 引擎各自为政 | 统一抽象 llama.cpp / vLLM / Diffusers 三种后端 |
| 模型类型繁多 | 覆盖 Chat / Embedding / ASR / TTS / Reranker / OCR / T2I / T2V / I2V 共 9 类 |
| 调用方式不统一 | 提供 OpenAI 兼容 API（chat / embeddings / images / videos） |
| 缺乏可视化管理 | Web UI 提供 Dashboard / Models / GPU 监控 / Playground / Settings |
| 换引擎成本高 | 同一模型可一键切换后端引擎 |
| 显存难控制 | Diffusers 支持 FP8 量化 + CPU Offload，显存占用可大幅下降 |

---

## 二、核心特性

### 1. 混合推理引擎架构

这是 AMM 最核心的设计。它不绑定某一种推理框架，而是把引擎做成可插拔：

- **llama.cpp（GGUF）**：适合 Chat / Embedding / ASR / TTS / Reranker / OCR，部署轻、显存友好；
- **vLLM（safetensors）**：高吞吐、高并发，适合作为主力服务端推理；
- **Diffusers（ModelScope）**：负责文生图 T2I、文生视频 T2V、图生视频 I2V 等生成式任务。

底层通过统一的 **Engine Registry** 注册，前端切换引擎只需调一个接口。

### 2. 9 类模型统一接入

文本、语音、视觉类模型全部抽象成"模型实例"，生命周期（启动 / 停止 / 重启）由 **Model Manager** 统一管理，不再需要你手动为每个模型维护进程。

### 3. OpenAI 兼容 API

对外提供 OpenAI 风格接口，**现有调用 OpenAI 的代码几乎不用改就能接**：

- `/v1/chat/completions` —— 对话
- `/v1/embeddings` —— 文本嵌入
- `/v1/images/generations` —— 文生图
- `/v1/videos/generations` —— 视频生成（支持 `video_type=t2v | i2v`）

### 4. 面向大显存的量化与卸载策略

生成式模型通常很吃显存。例如 Wan2.2 这类 MoE 双专家视频模型，全 BF16 权重接近 75G，单卡几乎跑不动。AMM 的 Diffusers 引擎支持：

- **FP8 存储 + BF16 计算**（layerwise_casting）：显存可压到约 28G；
- **CPU Offload / Group Offload**：按叶子节点把 Transformer 切成 CPU/GPU 分载，进一步缓解显存压力。

这些都可以在 **Web 前端 Advanced 设置**里一键切换（Quant / Compute Dtype / Boundary Ratio / CPU Offload），不用改代码。

### 5. 实时监控与浅色主题

- GPU / CPU / 内存 / 磁盘占用实时看板，模型运行状态实时刷新；
- 🌙 / ☀️ 浅色深色主题一键切换，偏好本地持久化。

---

## 三、整体架构

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

三层结构清晰：

- **前端**：负责展示与管理；
- **后端核心**：引擎注册表 + 版本管理 + 生命周期管理；
- **引擎层**：llama.cpp / vLLM / Diffusers 三种可插拔实现。

---

## 四、快速开始

### 环境要求

- Linux（Rocky Linux 9 / Ubuntu 22.04 / Debian 12）
- Python 3.11+
- NVIDIA GPU（建议显存 ≥ 48G）
- CUDA 13.2+（需支持 Blackwell sm_120，项目使用 torch 2.11.0+cu130）

### 安装与启动

```bash
git clone https://github.com/xztjhs/amm.git /amm
cd /amm

# 首次安装
bash deploy/install.sh

# 启动服务
export PYTHONPATH=/amm
export AMM_ROOT=/amm
export MODELS_DIR=/models
python3.11 /amm/backend/server.py
```

启动后浏览器访问 `http://<服务器IP>:18080/` 即可打开管理界面。

> 也支持 Docker Compose 一键部署，见 `deploy/` 目录。

### 配置模型

模型通过 `backend/config/models_config.yaml` 声明，例如：

```yaml
chat_model:
  id: "chat"
  engine_type: "llama_cpp"   # 或 "vllm"
  available_models:
    - name: "Qwen3.6-35B"
      file: "<你的 GGUF 文件路径>"
```

模型文件统一放在 `/models/` 目录。

---

## 五、引擎支持矩阵

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

可以看到：文本/语音/OCR 走 llama.cpp / vLLM，生成类走 Diffusers，各取所长。

---

## 六、路线图

目前已实现：

- [x] 混合引擎架构（llama.cpp / vLLM / Diffusers）
- [x] 引擎版本管理（安装 / 卸载）
- [x] 9 类模型统一配置
- [x] OpenAI 兼容 API（chat / embeddings / images / videos）
- [x] Web 前端（Dashboard / Playground / GPU 监控 / Settings）
- [x] vLLM CUDA13 + Blackwell 验证
- [x] Diffusers FP8 layerwise casting（Wan2.2 MoE）
- [x] Diffusers T2I 推理验证（Qwen-Image）

规划中：

- [ ] Diffusers T2V / I2V 完全验证
- [ ] 模型自动下载（HuggingFace / ModelScope）
- [ ] 权限管理与多用户
- [ ] 模型量化转换工具（GGUF）

---

## 七、技术亮点总结

1. **引擎与模型解耦**：一份配置、多引擎可切；
2. **API 标准化**：OpenAI 兼容，迁移成本低；
3. **生成式模型显存攻坚**：FP8 + 层卸载让大体积生成模型在小显存上可落地；
4. **全栈可控**：从部署到可视化监控到推理调用，一条龙。

---

## 写在最后

AMM 是一个仍处于快速迭代阶段的开源项目，主打"一套平台管所有模型"。如果你也厌倦了本地一堆模型进程手忙脚乱，欢迎去 GitHub 看看、点个 Star，或在评论区交流你的部署经验。

- GitHub（开源地址）：https://github.com/xztjhs/amm
- 开源协议：MIT License

本文为作者原创技术介绍，旨在交流开源软件，不构成任何商业建议。
