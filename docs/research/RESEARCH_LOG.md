# AMM 研发过程档案（Research & Development Log）

> 本目录汇总 AMM 平台从立项到各验证节点的研发过程、阶段成果、关键决策、踩坑与规范。
> 归档时间：2026-08-05

---

## 一、项目概览

**AMM（AI Models Manage）** —— 基于混合推理引擎架构的 AI 模型统一管理与调度平台。

- **仓库**：Gitea `iei/amm`
- **验证机**：192.168.100.245 容器（SSH 62022, root/admin@123）
- **GPU**：NVIDIA RTX 6000D ×1（84GB 显存，Blackwell，compute capability 12.0 / sm_120）
- **对外端口**：宿主 60008 → 容器 8080（所有 OpenAI 请求统一走 60008）
- **Web UI**：http://192.168.100.245:60006/

### 混合推理引擎
| 引擎 | 类型 | 适用类别 |
|------|------|---------|
| llama.cpp | GGUF | Chat / Embedding / ASR / TTS / OCR / Reranker |
| vLLM | safetensors (HF) | Chat / Embedding（高并发） |
| Diffusers | HF/ModelScope | T2I / T2V / I2V |

### 9 类模型
Chat / Embedding / ASR / TTS / Reranker / OCR / T2I / T2V / I2V

---

## 二、项目演进时间线

### 2026-08-04 — 立项与初始化
- 在 Gitea 创建 `iei/amm` 仓库，初始化混合引擎架构代码
- 首个提交 `893e9b9`（26 文件，4647 行）
- 建立每 30 分钟自动推送 cron（WIP 进度自动同步 Gitea）

### 2026-08-05 — 核心开发 + 引擎验证
- **混合引擎架构**：EngineRegistry / VersionManager / ModelManager 抽象层
- **API**：OpenAI 兼容（/v1/models, chat, embeddings）+ Diffusers 桥接 + 9 类模型生命周期
- **前端**：Dashboard + Playground UI
- **per验证链路**（见下节）

---

## 三、验证进度与结果

### ✅ 已通过验证（按时间顺序）

| # | 能力 | 引擎 | 模型 | 说明 |
|---|------|------|------|------|
| 1 | Chat/LLM | llama_cpp b4727 | Qwen3.6-35B-A3B (GGUF) | 上下文/多轮推理正常 |
| 2 | Embedding | llama_cpp | Qwen3-Embedding-8B (GGUF) | 文本向量化 |
| 3 | ASR | llama_cpp + mmproj | Qwen3-ASR-1.7B | /v1/audio/transcriptions |
| 4 | TTS | llama-tts CLI | Qwen3-TTS-12Hz (ModelScope) | /v1/audio/speech，backbone+mmproj |
| 5 | OCR | llama_cpp + mmproj | DeepSeek-OCR-2 | /v1/ocr |
| 6 | **vLLM** | **vLLM 0.22.1 (CUDA13)** | **Qwen3-4B (safetensors)** | **Blackwell sm_120 全链路通过** |

### ⏳ 待验证
- **Diffusers** 文生图 / 文生视频 / 图生视频（引擎待安装）

---

## 四、关键研发节点：vLLM + Blackwell（CUDA 13）攻坚

这是本阶段最具价值的技术攻坚，完整记录了从「cu12 不可用」到「CUDA 13 全链路验证通过」的全过程。

### 4.1 问题背景与根因
- 初始部署 vLLM 0.8.5：内置 `torch 2.6.0+cu124`（**cu12 系列**），编译 kernel 最高支持到 **sm_90**
- RTX 6000D 是 **sm_120**（Blackwell）
- 启动报错：`CUDA error: no kernel image is available for execution on the device`
- **CUDA 13 铁律**（老板 2026-08-05 定）：本机 CUDA 13.2，**严禁 cu12**，torch/vLLM 必须 cu13 且支持 Blackwell

### 4.2 解决方案
1. 新建独立 venv：`vllm/0.22.1`
2. 安装 **torch 2.11.0+cu130**（from pytorch cu130 源，arch 含 sm_120）
3. 安装 **vLLM 0.22.1** + 完整 CUDA 13 依赖（cudnn-cu13 / nccl-cu13 / cutlass-cu13 等）
4. 模型：vLLM 不支持 GGUF 的 qwen35moe 架构 → 用 ModelScope 下 **Qwen3-4B**（safetensors）

### 4.3 安装命令（可复现）
```bash
# torch（cu130）
pip install "torch==2.11.0+cu130" \
  --index-url https://mirrors.aliyun.com/pypi/simple/ \
  --extra-index-url https://download.pytorch.org/whl/cu130
# vllm
pip install "vllm==0.22.1" \
  --index-url https://mirrors.aliyun.com/pypi/simple/ \
  --extra-index-url https://download.pytorch.org/whl/cu130
```

### 4.4 验证结果
- `torch.cuda.get_arch_list()` 含 **sm_120** ✅
- vLLM API server 在 Blackwell 上成功加载 Qwen3-4B
- 外部访问（宿主 60008）→ AMM → vLLM 全链路推理返回正确回复 ✅

---

## 五、踩坑与经验（重要）

1. **CUDA 13 铁律**：CUDA 13.2 严禁 cu12。判断依据 `torch.version.cuda >= 13`
2. **venv 调用必须用绝对路径**：`venv/bin/python3.11`；`cd` + 裸 `python` 会命中系统 PATH，把包装到系统
3. **vLLM 需 HF safetensors 模型**：GGUF 的 `qwen35moe` 架构不支持
4. **混合源安装**：通用依赖走阿里云镜像，torch/torchvision 走 pytorch cu130 源
5. **网络绕坑**：个别包（numpy 等）从国外 files.pythonhosted 下载超时 → 先从阿里云单独预装到 venv 再装 vllm
6. **gpu_memory_utilization**：需调低至 ~0.65（机器存在显存基线占用，0.9 会导致启动失败）
7. **AMM bridge bug**：`/v1/chat/completions` 路由实际指向 `chat_completions_stream`，需用 `_rewrite_vllm_model` 把 AMM id(chat) 重写为 vLLM 路径名，否则报 `model chat does not exist`(404)
8. **AMM 进程残留**：vLLM 含 `VLLM::EngineCore` 子进程，stop 不净需 `kill -9`，否则端口 18081 被占、显存不释放

---

## 六、项目规范（老板制定）

1. **模型资源**：优先用 ModelScope，替代 Hugging Face
2. **软件源**：Python 等优先用国内镜像（阿里云、清华 TUNA 等）
3. **推理引擎优先级**：vllm > llama.cpp > Diffusers
4. **CUDA 13 铁律**：严禁 cu12，必须支持 Blackwell sm_120
5. **显存铁律**：每类模型部署/验证只开 1 个、依次串行，避免 84GB 不够

---

## 七、日志附件说明

- `deploy/logs/` — 容器运行日志（AMM server / chat / asr / embedding / ocr / tts）
- `deploy/logs/install/` — 安装构建日志（CUDA 13 安装、llama.cpp 构建、vLLM/torch 安装、ModelScope 下载）

> ⚠️ 日志含环境信息，如不需公开可删除 `deploy/logs/` 后重建（.gitignore 默认忽略 logs/）。
