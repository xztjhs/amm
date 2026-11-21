# AMM 项目说明

> **AMM = AI Models Manage** — 一套跑在自己机器上的 AI 模型统一管理与推理调度平台。
> 当前版本：**v1.4** ｜ 文档日期：2026-11-15 ｜ 代码仓库：`admin/AMM`（Gitea，私有）

---

## 1. 一句话说清 AMM 是什么

**在一个网页里，统一管理、启动、配置、测试和对外提供所有主流 AI 模型——把「跑模型」变成点几下按钮的事。**

传统跑一个本地大模型要经历：装 Python / 装 CUDA / 编译引擎 / 拼启动命令行 / 调几十个参数 / 自己写测试脚本 / 再为向量、语音、图片、视频各来一套。AMM 把这些全部收进一个 Web 控制台：

```
选类别 → 选引擎 → 选模型文件 → 调参数 → 启动 → 在 Playground 里测 → 拿到 OpenAI 兼容 API
```

---

## 2. 解决什么问题

| 痛点 | 现状 | AMM 的做法 |
|---|---|---|
| 引擎各自为政 | llama.cpp / vLLM / Diffusers 命令、参数、目录全不同 | 统一抽象成「引擎」，同一套界面切换 |
| 模型类型多 | 文本、向量、语音、图像、视频各要一套流程 | 9 类模型一套配置 + 一套 Playground |
| 参数记不住 | 每个引擎几十个 flag，改一次翻一次文档 | 表单驱动，参数定义在 yaml，加 `--reasoning-budget` 等一键开关 |
| 端口/进程混乱 | 手起进程，忘了关，端口冲突 | 端口池自动分配 + 实例生命周期管理 + SQLite 持久化 |
| 对外接入麻烦 | 各家 SDK 写法不一 | 原生 OpenAI 兼容 `/v1/*`，任何客户端改 base_url 即可 |
| 显存不够 | 大模型动辄跑不起来 | 量化转换（GGUF 17 种）+ vLLM/llama.cpp CPU offload + Diffusers FP8 |
| 多供应商成本看不清 | 本地 + 云端混用，用量无处统计 | Token Router：provider 汇聚 + 虚拟 key + 分模型计费报表 |

---

## 3. 核心能力

### 3.1 九类模型，一套界面
Chat/LLM/VLM ｜ Embedding ｜ ASR ｜ TTS ｜ Reranker ｜ OCR ｜ T2I ｜ T2V ｜ I2V
覆盖 **文本 / 向量 / 语音 / 图像 / 视频** 全模态。

### 3.2 四种推理引擎，自由切换
| 引擎 | 形态 | 覆盖类别 | 特点 |
|---|---|---|---|
| **llama.cpp** | 源码编译，`cpu` / `cuda` 双 flavor | chat / embedding / asr / tts / reranker / ocr | GGUF 量化，省显存，中小模型首选 |
| **vLLM** | pip venv，多版本共存 | chat / embedding | 高并发、高吞吐，服务化首选 |
| **Diffusers** | pip venv | image / video | 文生图 / 文生视频 / 图生视频，支持单文件 GGUF 权重与 FP8 |
| **sentence-transformers** | pip venv | embedding / reranker | 与 HF 生态对齐的向量/精排 |

引擎版本在网页端就能装、卸、换：llama.cpp 版本取自 GitHub Releases（`b#####`），vLLM / Diffusers / sentence-transformers 取自 PyPI；支持「装新版 → 起新实例 → 探活 → 切换 → 失败回滚」的蓝绿替换，引擎目录挂在宿主机 `AMM_ENGINES_ROOT`，换引擎不用重建镜像。

### 3.3 动态实例 + 端口池
- 同类别可并发多实例，端口池 **18100–18499** 自动分配，9 类固定场景（18081–18089）保留不变。
- 实例落 SQLite（`$AMM_ROOT/data/amm.db`），服务重启自动恢复。
- 启动 / 停止 / 重启 / 结束运行，Dashboard、Running Models、启动向导三处行为一致。

### 3.4 统一启动向导
类别 → 引擎 → 版本/flavor → 模型文件 → 参数 → 启动，走完可直接「存为模版」。
参数表单由 `backend/config/models_config.yaml` 生成（number / float / select / boolean / string / mmproj）。
模版放在 `templates/<category>/*.yaml`，内置 14 个（Qwen3 系、BGE、Whisper、CosyVoice2、Wan2.2 等），支持新建 / 编辑 / 复制 / 删除 / 一键部署。

### 3.5 原生 OpenAI 兼容
统一入口 `http://<host>:60006`：

```
GET  /v1/models                  POST /v1/chat/completions
GET  /v1/models/{id}             POST /v1/completions
POST /v1/embeddings              POST /v1/rerank
POST /v1/audio/transcriptions    POST /v1/audio/speech
POST /v1/audio/translations      POST /v1/ocr
POST /v1/images/generations      POST /v1/images/edits
POST /v1/videos/generations      /v1/files* · /v1/batches*
```

OpenAI 客户端只需把 `base_url` 填成 `http://<host>:60006/v1`。
`openai_bridge` 会自动清洗 JSON-Schema（例如补齐 `pattern` 的 `^` 锚定），修掉常见 `HTTP 400`，工具调用 / 结构化输出完整保留。

### 3.6 Token Router（汇聚 + 计费）
- **Provider 汇聚**：openai / anthropic / deepseek / icompify / 自定义，填 Base URL + Key 点「拉取模型」自动同步可用模型；Key 可只写环境变量名。
- **虚拟 Key**：签发 / 编辑 / 吊销、RPM 与总 token 限额、模型白名单、过期时间、事后一次性复制明文。
- **路由策略**：前缀强制、本地优先、provider 权重轮询。
- **计费报表**：人民币 元 / 百万 tokens，按 key / 按模型统计，采集 e2e / TTFT / ITL / TPOT / TPS，支持 CSV 导出（带 BOM，Excel 直接开）。

### 3.7 Playground
9 类模型都能在页面里实测：Chat 流式输出并统计 TTFT / TPS / 端到端；Embedding / Rerank / ASR / TTS / OCR / T2I / T2V / I2V 从已部署实例里下拉选择，测完即见结果与耗时。

### 3.8 运维能力
- **GPU Monitor**：型号 / 计算利用率 / 显存 / 带宽 / 温度 / 风扇 / 功耗 / 时钟 / PCIe。
- **Log Center**：多来源聚合、级别与关键字过滤、自动刷新、下载；兼容 llama.cpp 原生日志格式（相对时间 + 单字母级别）。
- **Tools**：模型自动下载（ModelScope / HuggingFace，断点续传 + 速度显示 + HF 镜像与 token + HTTP 代理）、GGUF 量化（17 种类型）、HF 格式转换。
- **Settings**：节点名、热重载配置、安全重启、日志下载、引擎管理。

---

## 4. 技术架构

```
┌──────────────────────────────────────────────┐
│            AMM Web UI（无构建，原生三件套）     │
│  Dashboard │ Launch │ Running │ Playground    │
│  GPU │ Logs │ Router │ Tools │ Settings       │
└──────────────────────┬───────────────────────┘
                       │ HTTP  REST / SSE
┌──────────────────────▼───────────────────────┐
│        AMM 主服务（Python 3.11 · aiohttp）     │
│  server.py ── /api/* 管理面 ── /v1/* 推理面    │
│  ┌──────────┬──────────┬──────────────┬──────┐│
│  │ Engine   │ Instance │ ModelManager │Router││
│  │ Catalog  │ Store    │ (生命周期)    │(计费)││
│  └────┬─────┴────┬─────┴──────┬───────┴──────┘│
└───────┼──────────┼────────────┼───────────────┘
        ▼          ▼            ▼
   llama.cpp     vLLM      Diffusers / sentencetransformers
   子进程:18081-18499（每实例一进程）
```

- **后端**：Python 3.11 + aiohttp，40 个模块约 12.5k 行；配置 yaml，状态 SQLite / json（原子写）。
- **前端**：原生 HTML + CSS + JS，约 7.3k 行，**无 npm、无构建**，改完刷新即见；深浅双主题。
- **测试**：pytest **171 个用例**（API 回归、动态实例、引擎 flavor、蓝绿替换、日志中心、协议扩展、Router、模版），用例间隔离状态。
- **部署**：Docker + NVIDIA runtime；宿主映射 `60006 -> 80`。

---

## 5. 目录结构

```
/amm/
├── backend/
│   ├── server.py            # 主服务入口（路由 / 生命周期）
│   ├── api/                 # 各类桥接：openai / diffusers / download / templates / router / engine / fs / logs
│   ├── core/                # engine_catalog / instance_store / model_manager / port_allocator / router / templates
│   ├── engines/             # llama_cpp / vllm / diffusers / sentence_transformers / quantizer
│   ├── config/              # models_config.yaml（模型与参数定义）
│   └── tests/               # pytest 用例
├── frontend/                # index.html + js/app.js + css/style.css + assets/
├── templates/               # 8 类内置模版 yaml
├── deploy/                  # 安装 / 容器启动 / Unraid 标准容器 / 引擎安装脚本
├── docs/                    # 手册、迭代方案、维护记录、截图
├── data/                    # amm.db / router.json / 下载与任务状态（运行时生成）
├── logs/                    # 运行日志
└── /models/                 # 模型文件根目录（容器内挂载）
```

---

## 6. 快速开始

### 环境要求
- Linux（Rocky Linux 9 / Ubuntu 22.04 / Debian 12）
- Python 3.11+
- NVIDIA GPU（推荐显存 ≥24G，48G+ 更适合多实例 / 视频模型）
- CUDA 13.x（Blackwell `sm_120` 需 torch 2.11 + cu130）

### 启动
```bash
# 1) 容器
cd deploy && docker compose up -d
docker exec -it amm-server bash

# 2) 容器内安装依赖与引擎
bash /amm/deploy/install.sh

# 3) 起服务
export PYTHONPATH=/amm AMM_ROOT=/amm MODELS_DIR=/models
python3.11 /amm/backend/server.py
```

浏览器打开 `http://<服务器IP>:60006/`，无登录直接进控制台。

### Unraid（生产环境当前用法）
按 `docs/部署-Unraid标准容器.md` 以 dockerMan 标准容器运行（容器名 `amm`，`60008 -> 80`，`--runtime nvidia`），Docker 页面可见图标、可启停、可模板重建。

---

## 7. 版本现状

| 版本 | 主要内容 |
|---|---|
| v1.4 | embedding/reranker 参数自动补齐；`/v1/embeddings` 上游错误中文可执行提示；弹窗头部吸顶 + 模型文件可换 |
| v1.3 | Token Router 重构（拉模型 / 编辑 / 定价 / 计费 / 报表）；运行中改参数即时生效；节点名可配 |
| v1.2 | 引擎安装浮动窗；llama.cpp CUDA 工具链自愈（自动补 libnvvm）；Playground 按实例测；日志配色重做 |
| v1.1 | 引擎目录（GitHub/PyPI 版本源 + cpu/cuda flavor）；统一启动向导；模版 CRUD；列表式 Dashboard |
| v1.0 | 动态实例 + SQLite；sentence-transformers 引擎；蓝绿换引擎；模版系统；Provider 汇聚与路由；OpenAI 协议收口 |
| v0.2–v0.9 | 混合引擎架构起步、Diffusers T2V/I2V 打通、下载与量化、日志中心雏形、用户手册 |

完整变更见 [`CHANGELOG.md`](../CHANGELOG.md)。

---

## 8. 适用场景

- **团队内网自建 MaaS**：一台 GPU 机器跑多模型，统一出口给业务方，按 key 计费。
- **RAG 基础设施**：embedding + reranker + chat 一条链在本机跑完，数据不出内网。
- **多模态试验台**：文本、语音、图像、视频在同一界面反复对比参数与性能。
- **Agent / Skill 后端**：任何 OpenAI 兼容客户端指过来即可，工具调用不用额外适配。
- **私有化交付**：容器 + 挂载目录即可搬走，引擎与模型都可离线安装。

不适用：需要多租户强隔离的公有云 SaaS（当前无权限体系，`多用户权限管理` 在路线图上）。

---

## 9. 路线图

- [ ] 权限管理与多用户
- [ ] 实例级限流与队列
- [ ] 更完整的可观测性（Prometheus 指标导出）
- [ ] 模版市场 / 一键分享

---

## 10. 联系

- **负责人**：撞强 — xt@pcd.name ｜ 微信 13808230006
- **MaaS 服务供应商**：北京积算（icompify）— <https://www.icompify.com/>
- **许可证**：MIT
