# AMM Changelog

## v1.9.4 — 2026-11-19（Unraid 标准容器管理 + 文档沉淀）

### 新增
- **`docs/运维操作手册.md`** — 日常维护总入口：启停/升级/重建镜像/引擎管理/
  排障/备份恢复，每条命令都实测过。
- **`docs/CUDA与引擎安装避坑指南.md`** — 编译 CUDA 引擎的六类坑
  （内嵌 UI 构建失败、工具链四层缺失、prerelease 过滤、实例端口、日志爆炸、分支漂移）。
- **`deploy/sync-unraid-template.sh`** — 同步 Unraid 模板并按模板重建容器。

### 修复
- **AMM 此前不是「标准受管容器」**：容器上没有任何 dockerMan 标签
  （`managed`/`webui`/`icon` 全空），在 Unraid Docker 页里能跑但
  **没图标、没 WebUI 按钮、不能用界面启停/编辑/重建**。
  改为参照同机 ACD 的做法，注入三个标签，现已与 ACD/Jellyfin 并列。
- **模板 `<WebUI>` 端口写错**：写的是 `[PORT:80]`（容器内端口），
  实际映射是 60008，导致 WebUI 按钮连到错误端口。
- **图标依赖外网且过大**：原指向 GitHub raw（1.9MB，内网取不到就显示问号）。
  改为 AMM 自托管 `http://<host>:60008/assets/logo-128.png`（19KB）。
  查过 `DockerClient.php::getIcon()`：它把 Icon 原样交给 `download_url()`，
  **不做 `[IP]`/`[PORT]` 占位符替换**，模板里必须写实际可达地址。

## v1.9.3 — 2026-11-18（CUDA 工具链入镜像 + 日志治理）

### 新增
- **镜像自带完整 CUDA 工具链**：`deploy/cuda-bundle/` 补齐 `nvcc-bin`、
  `targets-include`、`targets-lib`、`stubs/libcuda.so`。
  此前镜像只有 CUDA **运行时**，在 Web 上装 llama.cpp 的 cuda flavor 必然失败。
- **`deploy/export-cuda-bundle.sh`** — 从现成容器导出 bundle，含完整性前置校验。
- **`deploy/.dockerignore`** — 构建上下文排除 `data/`、`logs/`、`.git`。
- **Dockerfile 构建期 nvcc 自检** — 编不过就在构建期失败，
  不会等到用户点「安装引擎」才发现。
- **Dashboard 新增 CPU 推理进程监控** — PID/程序/命令行/CPU/内存/线程/运行时长，
  按 `-ngl`/`-cpu` 精确区分 CPU 与 GPU 进程。

### 修复
- **CUDA 工具链四层缺失**（逐层暴露，很容易误判成新问题）：
  `nvcc` → `libcudart_static.a`/`libcudadevrt.a` → `targets/.../include`
  → `stubs/libcuda.so`（cmake `CUDA::cuda_driver`）。
  已在 `_ensure_cuda_toolchain` 里全部自动补齐。
- **llama.cpp v1.0.0 起构建必失败**：新版给 llama-server 内嵌 Web UI，
  `add_dependencies(... llama-ui-assets)` 硬编码，校验强制要求
  `workbox[hash].js` 而官方包没这个文件。已加 `-DLLAMA_BUILD_UI=OFF`。
  **注意此开关只对全新 build 目录有效**，旧目录残留会导致仍然失败。
- **`amm_server.out` 每天涨 150MB**：entrypoint 用 `nohup >> .out` 不轮转，
  root logger 是 INFO 级，前端 0.5s 轮询全落进去。
  改为 `.out` 只留 WARNING、访问日志独立到 `access.log` 并过滤健康轮询。
  详见 `backend/tests/test_access_log_filter.py`。
- **访问日志过滤器静默失效**：aiohttp 的 `AccessLogger.log` 是
  `logger.info(fmt % values)`，`record.args` 为空，只查 args 的过滤器等于没生效。

## v1.9.2 — 2026-11-17（UI 维护 8 项）

### 新增
- **Models 页合并**：「启动新实例」与「运行中实例」合成单页「实例管理」
  （统计条 → 完整实例表 → 折叠式启动向导），只剩「实例管理 / 模版库」两个子标签。
- **Settings 搜索**：搜索框 + 6 个分区索引 chip + 全部展开/收起，
  命中高亮、跳转闪烁、命中计数。
- **文件管理根目录切换**：`/amm`、`/models`、日志、数据 四个一键按钮。
- **Dashboard CPU 推理进程监控**（见 v1.9.3）。

### 修复
- **实例列表只显示动态实例**：9 类固定实例（chat/embedding/asr/tts/reranker/
  ocr/t2i/t2v/i2v）全都看不见。改为以 `/instances` 为全集。
- **llama.cpp 上游版本刷屏**：`b#####` 在 GitHub 上全是 prerelease，
  原实现只取最新 25 条（全是 b 版本），过滤后为空又回退成不过滤。
  改为逐页翻 + 无正式版时返回空列表。现只显示 `v1.0.0`/`v0.9.0`/`v0.8.0`。
- **模型文件浏览弹窗被遮挡**：`#fileBrowserModal` 的 z-index 从 200 提到 500。
- **顶部 GPU Monitor 菜单已移除**，Dashboard 里的「GPU 状态」保留。
- **模型配置同步**：`/models/amm` 下用户重命名后，`models_config.yaml`
  与 6 个模版的 `model_file`/`mmproj` 全部改指 `amm/`。

## v1.9.1 — 2026-11-17（Unraid 标准容器部署）

> 容器与镜像被清空后从零重建，形成可复现的 Unraid 部署流程。

### 新增
- **`deploy/Dockerfile.local-cuda`** — 用本地 CUDA 组件包构建，不再现场下载 RPM。
  原 `Dockerfile.unraid` 从 NVIDIA 源拉 158MB 的 cublas 反复 SSL 断流
  （`curl: (56)`），构建十次九次失败。
- **`deploy/cuda-bundle/`** — CUDA 组件包：cublas / cublasLt / nvblas /
  **libcudart** / cublas 头文件 / nvvm(cicc) / nvvm 运行库 / cccl。
- **`deploy/unraid/my-AMM.xml`** — dockerMan 模板，17 个配置项。

### 修复
- 镜像缺 `libcudart.so.13` 导致模型启动失败
  （`error while loading shared libraries`）。
  该库与 cublas 是不同包，只导 cublas 时镜像能构建、服务 health 也绿，
  但一启动模型就暴露。
- 构建上下文不能用 symlink 指向外部目录（`failed to compute cache key`），
  改为真实目录复制。

### 部署
- 容器名 `AMM`（大写），端口 60008:80
- 三个 Unraid label：`managed=dockerman` / `icon` / `webui`
- `--restart unless-stopped` + `HEALTHCHECK`
- 图标用 GitHub raw 直链（`/blob/` 返回 HTML，Unraid 无法识别）

### 验证
全新容器删除重建后，**不手动补任何库**，启动 embedding 实例 `ready=true`，
推理返回 1024 维向量，`nvidia-smi` 可见进程。

### 文档
- 重写 `docs/部署-Unraid标准容器.md`（276 行）
- 新增 `docs/维护记录-20261117-Unraid容器重建.md`（完整踩坑过程）
- `docs/开发经验沉淀-v1.9.md` 新增第九章（11 条 Unraid 构建经验）

---

## v1.9 — 2026-11-17（品牌化 / 文件管理 / 目录可配 / 鉴权开关 / 模块化）

> 本轮以「白盒 E2E 测试暴露的问题 + 用户可运维性」为驱动，共 10 项改造。
> 详细方案见 `docs/白盒测试方案-20261116.md` 与两份测试报告。

### 🎨 1. 品牌可编辑（#8 #10）
- LOGO 换成 `frontend/assets/logo.png`（2048×2048），顶栏与 favicon 同时使用。
- 新增 `GET/POST /api/settings/brand`：项目名称 / 副标题 / LOGO 路径。
- 前端 `applyBrand()` 同步顶栏、浏览器标题栏、favicon，**刷新后保持**。
- Settings 新增「项目品牌」面板，带实时预览与「恢复默认」。

### 🗂 2. 本地文件管理（#7）
- 新增 `backend/api/file_manager_bridge.py`：
  `roots / list / read / write / mkdir / upload / download / delete / rename`。
- **安全边界**：所有路径 `realpath` 归一化后必须落在允许根
  （`/amm`、`/models`、日志、数据、模版、verification）之下，
  防 `../` 穿越与符号链接逃逸；实测 `/etc/passwd`、`/root` 均 403。
- 删除默认进 `/amm/.trash`（可恢复），二次确认才永久删除。
- 文本编辑上限 2MB，二进制文件只允许下载不允许编辑；上传上限 512MB。
- 前端 `frontend/js/filemanager.js` + Settings 面板，同时可当目录选择器复用。

### 📂 3. 服务目录可配置（#9）
- 新增 `GET/POST /api/settings/paths`：models / logs / data / templates / verification。
- 校验必须是存在的绝对路径；host/port 不允许改（防把服务启动配置弄坏）。
- 修改后需重启生效，界面明确提示。

### 🔐 4. 完全关闭鉴权（#2）
- 新增 `POST /api/settings/auth/disable|enable`、`GET /api/settings/auth`。
- 关闭需 `{confirm:true}`，前端二次确认（confirm + 输入 YES）。
- **修正中间件语义**：是否鉴权只看 `auth_enforced` 单一开关，
  不再因为「存着虚拟 Key」就强制拦截（否则会出现「关闭了还是 401」）。

### ⏱ 5. launch 等待端口就绪（#3）
- 新增 `ModelManager.wait_until_ready()`，`POST /api/instances/launch`
  返回带 `ready` / `ready_wait_ms`。
- 默认等 30s（`AMM_LAUNCH_READY_TIMEOUT` 可调，0=旧行为）。
- 消除「launch 后立刻请求 500」的竞态：实测冷启动 252ms 就绪后请求直接 200。

### 📜 6. 日志轮转（#4）
- `RotatingFileHandler(maxBytes=50MB, backupCount=5)`，
  环境变量 `AMM_LOG_MAX_BYTES` / `AMM_LOG_BACKUPS` 可调。
- 原单文件曾增长到 174MB（0.5s 轮询约 30MB/小时）。

### 🐳 7. 容器自愈（#5 #6）
- 定位「两次退出无人处理」根因：宿主机 19:27 重启过，而 `RestartPolicy=no`。
- `docker update --restart unless-stopped`，Unraid 模板同步加 `--restart` 与 `--health-cmd`。
- `Dockerfile.unraid` 增加 `HEALTHCHECK`（python 探测 `/api/health`，不额外装 curl）。

### 🧩 8. 前端模块化（#1）
- `app.js` 5322 → 4857 行，抽出 `logcenter.js`(181) 与 `tools.js`(314)。
- 跨模块依赖统一走 `window`（core 导出 `AMM_BASE` + 基础工具）。
- 拆分脚本 `scripts/split_app_js.py` 可重跑。
- 静态资源版本号升到 `?v=2.0`，避免升级后浏览器用旧缓存。

### 🐛 9. 白盒测试暴露并修复的缺陷
| 缺陷 | 修复 |
|---|---|
| `/v1/embeddings`、`/v1/rerank` 不写调用历史 | 两个返回点补记，输出带维度/命中数 |
| `/api/engines/installed` 7-13 秒（rglob 扫 19 万文件） | mtime 键缓存 + 启动后台预热，降到 **3ms** |
| GPU Monitor 页有实现无导航入口 | 补 `data-tab="gpu"` |
| Playground「API Debug」无切换按钮 | 补 `data-pg="api"` |
| 前端「模型名称」建议值等于 UID | 新增 `wizModelIdentityRaw` 保留大小写 |
| 鉴权中间件判定与 `authenticate()` 不一致 | 统一为单一判定来源 |


## v1.1 — 2026-11-14（引擎目录 / 统一启动向导 / 模版 CRUD / 列表式 Dashboard）

> 详细方案见 `docs/迭代方案-v1.1-引擎版本与启动向导.md`

### 🔧 1. 推理引擎管理升级为「引擎目录」（需求 1）
- 新增 `backend/core/engine_catalog.py` + `backend/api/engine_bridge.py`。
- **版本来源**：llama.cpp 取自 GitHub Releases（tag `b#####`）；vLLM / Diffusers / sentence-transformers 取自 PyPI JSON。结果缓存 30 分钟（`data/engine_catalog_cache.json`），上游不可达时回退内置清单，页面不白屏。
- **flavor 区分**：llama.cpp 与 sentence-transformers 提供 `cpu` / `cuda` 两套独立安装；vLLM 与 Diffusers 仅 `cuda`（后端会拒绝 `flavor=cpu`）。
- **操作**：下载（源码/whl）、安装、卸载，全部走异步任务 `POST /api/engines/tasks`，`GET /api/engines/tasks` 返回进度与日志尾巴，前端可看实时日志。
- 新增 `GET /api/engines/catalog`、`GET /api/engines/installed`。
- 安装状态改由磁盘扫描判定（`$AMM_ENGINES_ROOT/<engine>/<version>-<flavor>/`），兼容镜像内置 `/usr/local/bin/llama-server`。

### 🧭 2. Launch Model / Running Models 统一启动向导（需求 2、5）
- 两个页面共用同一组件：**类别 → 引擎 → 版本/Flavor → 模型文件 → 参数 → 启动**。
- 参数表单直接取 `GET /api/schema/params?category=&engine=`（与旧 Models 页同一份 yaml 定义），支持 number/float/select/boolean/string 与 mmproj。
- 模型文件选择走 `GET /api/schema/models`，按引擎过滤扩展名、识别 `mmproj`、识别目录型模型（`config.json` / `model_index.json`），扫描有限深度 + 条数并跑在执行器里。
- 启动成功可一键「存为模版」（带当前引擎/版本/flavor/参数）。
- **移除旧 Models 页**：`tab-models` 与 `renderModelsDetail` 已删除；参数编辑、启动命令编排、文件浏览迁移到 Dashboard 实例详情弹窗。

### 📋 3. Dashboard Model Status 改列表式（需求 3）
- 3×3 卡片 → 行式列表（模型 / 类别 / 引擎 / 端口 / 状态 / 显存 / 运行时长 / 操作）。
- Start / Stop / Restart 抽成 `rowActions()`，Dashboard、Running Models、向导三处行为完全一致。
- 断点（1120px / 720px）里表头与数据行隐藏同一批单元格，不再错行；窗口宽度变化时导航整体折行而非截断。

### 🗂 4. 模版 CRUD（需求 4）
- 新增 `PUT /api/templates/{id}`（编辑，内置模版自动另存为 `-custom` 副本）与 `POST /api/templates/{id}/duplicate`（复制）。
- 新增全屏模版编辑器：基础信息 + 引擎（可多个，带版本/flavor/参数）+ 下载源 + 模型文件 + 上下文/显存提示。
- 模版详情页提供「一键部署 / 下载模型文件 / 编辑 / 复制 / 删除」。
- 保存时自动剔除空字段，yaml 更干净。

### 🤗 5. HuggingFace 镜像与 API Token（需求 7）
- 新增 `GET/POST /api/models/download/settings`，字段 `hf_endpoint`（默认 `https://hf-mirror.com`）与 `hf_token`。
- 版本查询与下载均注入 `HF_ENDPOINT` / `HF_TOKEN`；token 落盘到 `backend/config/download_settings.json`，界面只显示打码值（`hf_t…3456`），支持「清除」。

### 🎯 6. 演示模型切换（需求 6、8、9、10）
| 类别 | 演示模型 |
|---|---|
| Embedding | Qwen3-Embedding-0.6B（GGUF Q8_0 走 llama.cpp / safetensors 走 sentence-transformers） |
| Reranker | Qwen3-Reranker-0.6B |
| ASR | Qwen3-ASR-0.6B（含 mmproj 音频投影） |
| TTS | Qwen3-TTS-12Hz-0.6B-Base（GGUF 社区量化 + tokenizer） |
| OCR | Qwen3-VL-2B-Instruct-GGUF（Q4_K_M + mmproj） |
| Image | Qwen-Image Q2_K GGUF（`city96/Qwen-Image-gguf`） |

对应内置模版：`templates/*/qwen3-*.yaml`、`templates/image/qwen-image-gguf.yaml`。

### 🖼 7. Diffusers 支持单文件 / GGUF 权重
- `_resolve_local_path` 支持单文件 `.gguf` / `.safetensors` 与 HF cache 路径。
- `QwenImagePipeline.from_single_file` + `GGUFQuantizationConfig`；当前 diffusers 版本不支持时返回明确错误（提示升级 ≥0.35 或改用 safetensors 目录）。
- 实例选择的模型文件（向导里挑选）优先于 yaml 里的 `model_id`。

### ✅ 验收
- `pytest`：171 项全绿。
- 沙箱端到端（伪造 llama-server）：向导启动实例 → Running Models 显示 → 参数保存（ctx_size 32768→16384 已落盘）→ 生成启动命令 → 模版新建/编辑/复制/删除。

---

## v0.9 — 2026-08-09（llama.cpp 思考开关 + 工具调用/结构化输出兼容修复）

### 🧠 1. Chat 支持禁用思考/推理输出（no_reasoning）
- `models_config.yaml` chat/llama_cpp 新增 boolean `no_reasoning`（label=禁用思考/推理输出）。
- `engines/llama_cpp.py`：`no_reasoning` 开启时追加 `--reasoning off`，实现只输出 content（不输出 thinking）。
- 说明：llama.cpp b4727 不识别 `--no-reasoning`；思考开关为 `--reasoning [on|off|auto]`。

### 🛠 openai_bridge JSON-Schema 兼容（修复 OpenClaw 等客户端 400）
- **根因**：llama.cpp(b4727) 做 JSON Schema → grammar 转换时**要求 `pattern` 必须 `^…$` 锚定**；OpenClaw 生成的 tools/response_format json_schema 里带未锚定 `pattern`（如文件引用 schema `~(?:[^01]|$)`）→ 整段请求被拒 HTTP 400 `Pattern must start with '^' and end with '$'`。
- **修复**：`openai_bridge.py` 新增 `_sanitize_schema`，转发 llama-server 前递归剥离仅属“可选收窄”的关键字（`pattern`/`minLength`/`maxLength`/`minItems`/`maxItems`/`minContains`/`maxContains`），**不破坏 tools 与 response_format**，保留工具调用能力。
- **验证（线上）**：
  - `response_format json_schema`（原 400）→ 200，返回合法 JSON `{"summary":"ok"}`
  - `tools` 请求（含未锚定 pattern）→ 200，`finish_reason:tool_calls` 正常返回参数
- 已 `scp` 部署至 `/mnt/data/docker/app/amm/backend/api/openai_bridge.py` 并重启生效。

### 🏷 版本号统一
- `backend/server.py` `AMM_VERSION` 默认置为 `v0.9`（环境变量可覆盖），页脚 / Settings / API 全局生效。

---

## v0.8.1 — 2026-08-09（手册弹窗与渲染修复）
- `fix(manual)`: 修复用户手册弹窗布局与第 2 章打不开，移除搜索框。
- `fix(manual)`: 代码块多行折叠与内部超链接无法访问。

---

## v0.8.0 — 2026-08-09（用户手册上线 / 版本号统一 / 容器瘦身 / 部署配置仓库化）

### 📚 1. 用户手册上线（右上角帮助 + 搜索）
- 新增 `docs/manual/` 用户手册共 7 章：产品介绍 / WebUI 教程 / **API 零IT版** / **API Agent版** / 架构设计 / 问题记录 / 图片见木。
- WebUI 右上角新增「📚 手册」按钮 + 搜索框：
  - 后端新增 `GET /api/docs`（手册目录）与 `GET /api/docs/{file}`（文章 markdown）。
  - 前端弹窗分左右：左侧目录，右侧 Markdown 渲染；搜索框全库全文检索并高亮命中。
  - 页脚版本号由 `v0.2` 改为动态读取 `/api/settings`（当前 `v0.8.0`）。
- 浅色主题截图全套：`docs/screenshots/light-0*.png`。

### 🔐 2. 版本号统一（修复页脚/Settings 显示 v0.2 的过时 bug）
- `backend/server.py` 新增唯一版本常量 `AMM_VERSION`（默认 v0.8.0，可用环境变量覆盖）。
- `/api/settings` version 字段、Settings 页、页脚均从此读取，一处升级全局生效。

### 🧹 3. 容器瘦身（写层 16G → 220M）
- 清理 `/root/.cache/pip` 11G、`/root/.cache/vllm` 619M、`/tmp` 编译/解包残留 2.1G。
- 截断超大日志 `amm_server.log`(827M) 等，清旧版本备份目录。
- 详见 `docs/维护记录-20260809-容器清理与部署配置.md`。

### 🐳 4. amm-docker 独立仓库
- 将容器部署配置（docker-compose / 安装脚本 / 环境变量）抽离为独立仓库 `iei/amm-docker`，与主代码解耦。

---

## v0.7.3 — 2026-08-08（diffusers 高级参数/自动释放/Playground 三能力）

### 🐛 追加修复：Models 页 diffusers Advanced 区不显示
- **根因**：`renderAdvancedSettings` 是 `async` 函数，但在 `renderModelsDetail` 的同步模板字面量 `${...?renderAdvancedSettings(id):''}` 直接被求值 → 拿到的是 Promise，渲染成空。
- **修复**：模板改渲染 `<div id="adv-wrap-<id>">` 占位；渲染后对 engine=diffusers 的模型异步调用 `renderAdvancedSettings` 注入真实区，并 `bindParamChange` 联动显存估算。
- **验证**：t2i / t2v / i2v 三模型 Advanced 区（Quant/Compute/Offload/Boundary/CPU-Offload）均正常渲染。
- commit `6cafbc7`。

### ⚙️ docker-compose.yml 环境变量 (v0.7.3)
- 新增 `AMM_AUTO_RELEASE` / `AMM_START_PRELOAD` / `AMM_AUTO_RELEASE_GPU` / `AMM_ROOT` / `MODELS_DIR` / `PYTHONPATH`。

### 🧠 1. Models 页 diffusers 高级参数（t2i/t2v/i2v）
- Advanced 区支持 **Quant / Compute Dtype / Offload / Boundary Ratio / CPU Offload** 设置并回写 yaml，start/推理按参数生效；修复 offload 下拉不读模型实际值的 bug。
- t2i/t2v/i2v 参数 label 中英对照（label + label_en）。

### 💾 2. 每次推理后自动释放 GPU
- `t2i/t2v/i2v` 完成/异常共 6 处自动释放：`del pipe` → `gc` → 清 pipeline cache → `torch.cuda.empty_cache()`。
- 实测：推理时 55.9G → 完成后 **655M**（`[auto-release] keep_pipeline=否`）。
- 开关：`AMM_AUTO_RELEASE=full`(默认，彻底释放) / `cache`(保留模型)；`AMM_START_PRELOAD=1` 时 start 按参数预加载。

### 🎛 3. Playground 只读高级参数 + 显存估算（T2I/T2V/I2V）
- 三类模型 Playground 均新增「🎛 模型高级参数（模型级·推理只读）」展示当前 Quant/Compute/Offload/Boundary/CPU-Offload；可改的请求参数（尺寸/步数/数量/种子等）仍可改。
- 显存估算（`estimateT2iVram` / `estimateVideoVram`）：权重(存储精度) + 激活(latent tokens·分辨率·数量/帧数·步数) + 固定，随参数实时变化。

### 涉及文件
- `backend/core/model_manager.py`：start 可选预加载（AMM_START_PRELOAD）
- `backend/api/diffusers_bridge.py`：自动释放\(del pipe + unload + empty_cache\)，`_release_gpu`/`_maybe_release_gpu`
- `backend/config/models_config.yaml`：t2i/t2v/i2v 参数中英 label
- `frontend/js/app.js`：estimateVideoVram、refreshPgVram 泛化、双语 label
- `frontend/index.html` / `frontend/css/style.css`：pgVram/pgAdv 展示

## v0.7.1 — 2026-08-08（容器 supervisor 化 + diffusers 推理独立启停）

### 🐳 1. server.py 不再是 PID1 —— kill 容器不死
- **根因**：`docker-entrypoint.sh` 用 `exec python server.py`，server 成容器 PID1 → `kill server` 直接杀容器。
- **修复**：entrypoint 改 **supervisor 模式**（PID1 = 监控 wrapper）：
  - `trap` 转发 TERM/INT/HUP，优雅退出；
  - server 作为后台子进程，`wait` 检测退出 → 自动重启（`AMM_RESTART_DELAY` 可调）；
  - 容器内 `kill -TERM <pid>` 只重启 server，容器不退出。

### 🎨 2. t2i/t2v/i2v 推理与容器自启动解耦（解决 GPU 显存常驻）
- **根因**：diffusers pipeline 缓存进 server 进程后常驻 GPU，无独立启停；停推理=杀 server=现 死容器。
- **修复**：新增独立启停机制（伴装 supervisor 之后）：
  - `POST /api/bridge/diffusers/preload`：手动预加载模型到 GPU（warm-up，避免冷启动延迟）；
  - `POST /api/bridge/diffusers/unload`：卸载指定/全部 pipeline + `gc` + `torch.cuda.empty_cache()` 释放显存；
  - `deploy/diffusers_ctl.sh <t2i|t2v|i2v|all> <start|stop|restart|status>`：独立启停脚本；
  - `/status` 新增 `pipeline_cache` + `gpu_allocated_mb` 诊断字段。

### 涉及文件
- `deploy/docker-entrypoint.sh`：supervisor 化
- `deploy/diffusers_ctl.sh`：新增，diffusers 推理独立启停脚本
- `backend/api/diffusers_bridge.py`：`preload` / `unload` 接口 + status 增强

## v0.7 — 2026-08-08（Diffusers bug 修复 + 任务实时计时/落盘/下载）

针对 AMM 反馈 4 项问题修复：

### 🐛 1. t2i/t2v/i2v 日志为空（Logs tab）
- **根因**：diffusers 走内置桥接（非子进程），`build_command()` 返回空列表 → `start_model` 无 `{model}_server.log`，`get_model_logs` 恒空。
- **修复**：`diffusers_bridge.py` 新增 per-model 日志写入，pipeline 加载 / 任务提交 / 推理开始 / 完成 / 异常均写入 `logs/{model}.server.log`（与 UI `get_model_logs` 对齐）。Logs tab 现在能实时看到三类模型的运行日志。

### 🐛 2. t2v / i2v 发起测试 GPU 不工作、Dashboard 无工作进程
- **根因**：video 模型 `cpu_offload` 逻辑走 `enable_model_cpu_offload()`（序列 CPU offload），层被挪到 CPU、GPU 利用率趋 0，且为内置桥接（无子进程）→ `nvidia-smi compute-apps` 看不到活跃进程。
- **修复（关键）**：offload 策略显式化，新增 `offload` 字段 `gpu | model | group`：
  - `gpu`（默认）：全模型驻留 GPU（Wan2.2-A14B FP8 ~28G，84G 可驻）→ GPU 利用率最高；
  - `model`：`enable_model_cpu_offload()`（旧默认）；
  - `group`：leaf_level CPU offload（显存极紧兜底）。
  - 兼容旧 `cpu_offload` bool（true→group，false/缺省→gpu）。后端 `update_advanced_settings` 白名单新增 `offload`，可网页改。
- **Dashboard 可视化**：新增 `/api/bridge/diffusers/status` 暴露活动/最近任务（模型/进度/实时耗时/成功失败），前端 Dashboard 新增「Diffusers 推理任务」区块实时轮询展示（解决"看不到工作")。

### 🟫 3. Playground 发起 t2i/t2v/i2v 拿不到最终文件
- t2i/t2v/i2v 生成**默认落盘**到 `/amm/verification`（不再依赖前端 save_to_disk 勾选），响应带 `saved_paths`；
- 新增 `GET /api/bridge/diffusers/download?path=`（限定在 VERIFICATION_DIR 内防目录穿越），前端结果区给出「⬇ 下载文件」链接；
- 失败时把具体错误写进 per-model 日志（配合修复 1 可查）。

### ⏱ 4. Playground 实时计时（提交→成功拿到结果）
- 后端每个任务记录 `submitted_at / started_at / finished_at`，算 `elapsed_submit_to_run`、`elapsed_total`；
- 前端 Playground 三个生成过程实时显示「⏱ 用时 Ns」（500ms 刷新），完成后展示「提交到完成耗时 `${elapsed}s`」；
- Dashboard 推理任务区块同样实时显示每条任务已用时。

### 涉及文件
- `backend/api/diffusers_bridge.py`：per-model 日志、任务状态注册、offload 策略、默认落盘、status/download 路由
- `backend/core/model_manager.py`：ADVANCED_FIELDS 增加 `offload`
- `frontend/index.html`：Dashboard 推理任务区块
- `frontend/js/app.js`：实时计时、下载链接、dashboard 任务区、高级设置 offload 选择
- `frontend/css/style.css`：`.diff-task-*` 样式

## v0.6.4 — 2026-08-08（模型下载：代理 + 版本/文件选择 + 断点续传/进度）

### 🌐 模型自动下载增强（Tools → Model Download）
1. **HTTP 代理设置**：
   - 面板新增「HTTP 代理」开关 + 地址，可开启/关闭/保存；持久化到 `backend/config/download_proxy.json`。
   - 开启后下载（HF/ModelScope）经代理转发；测试代理 `http://<PROXY_HOST>:<PORT>`。
   - API: `GET/POST /api/models/download/proxy`。
2. **版本 + 文件选择**：
   - 「查询版本与文件」列出模型所有分支/revision(tag/branch)，每版本展示文件清单 + 各文件大小 + 合计。
   - 可单选文件子集下载（默认可全选）。API：`POST /api/models/downloads`。
   - HF 用 `HfApi.list_repo_refs + model_info + get_paths_info`；ModelScope 用分支/文件 API。
3. **下载体验**：
   - 断点续传（HF/ModelScope snapshot 原生 resume）。
   - 实时进度条 + 下载速度 + 剩余时间(ETA) + 当前文件；任务可取消。
   - 停滞保护：下载无进展 3 分钟自动终止提示重试（避免永久挂起）。
   - API：`POST /api/models/download`、`GET status`、`POST cancel`。

### 关键实现
- `backend/api/download_bridge.py`（重写 v0.5）：代理/版本查询/下载/进度追踪，子进程 + 心跳 JSON。
- `backend/api/_download_helper.py`（新增）：revisions 查询与 download 执行脚本(环境变量传参)。
- `frontend/index.html` / `frontend/js/app.js`：面板 UI + 交互；`css/style.css` 补 btn-xs。

### 实测（webui, 245:60006）
- ModelScope: `Qwen/Qwen1.5-0.5B`(1.19G) 下载完成，进度/速度(3-7MB/s)/进度条实时显示；重下已存在模型秒 100% 完成(断点续传)。
- HF 经代理: 版本/文件查询成功，下载可用但代理 CDN 带宽低（HF 未认证限速+代理限制）；HF 大文件经该代理带宽极差是网络环境限制。
- 修复：下载进度统计改为仅统计目标模型子目录(避免历史模型干扰)；status 列表排序 key `created_at`；停滞检测改 180s 防慢代理误杀。

## v0.6.3 — 2026-08-08（Tools 三项模型工具修复）

### 🧰 Tools 菜单三项功能 webui 实测 + 修复
在独立的 Tools 菜单下，通过真实 Web UI 完整走查 模型自动下载 / GGUF 量化 / vLLM→GGUF 转换，修复 4 处 bug：

- **前端：量化/转换文件浏览器回调未暴露 to window**（`frontend/js/app.js`）
  - `qLoadDir / qPickFile / qPickDirTarget / qUp / qNewDir / vgPickSrc` 等 HTML onclick 依赖的闭包函数从未挂到 `window`，导致点目录/选文件全部无反应。补全 `window.q*` 绑定。
- **前端：GGUF 过滤正则错误**（`app.js`）
  - `/^\\.gguf$/i` 双反斜杠匹配含字符的 `\\.gguf`，对 `.gguf` 恒不匹配 → 源目录永远“无 GGUF 文件”。改为单反斜杠 `/^\.gguf$/i`。
- **前端：量化源“浏览”按钮 onclick 拼写错误**（`index.html`）
  - `openQuantSrcBrowser()` 缺 “ize”，改为 `openQuantizeSrcBrowser()`。
- **后端：QuantizerBridge 缺 `_resolve`**（`engines/quantizer.py`）
  - `quantize()`/`convert_hf()` 调用不存在的 `self._resolve` → POST /api/quantize、/api/convert/hf 一律 500。补上文件路径解析方法。
- **后端：status 排序 key 错误**（`quantizer.py`）
  - `x["created"]` 但 `to_dict()` 输出 `created_at` → GET /api/quantize/status 恒 500。改为 `x.get("created_at")`。
- **后端：vLLM→GGUF 用错 gguf 库**（`quantizer.py`）
  - `_run_convert_hf` 设置 `NO_LOCAL_GGUF=1` 导致禁用 llama.cpp b4727 自带 `scripts/gguf-py`，`import gguf` 落到 vLLM venv 旧版 0.19（无 `MODEL_ARCH.DFLASH`）→ AttributeError。移除 `NO_LOCAL_GGUF`。

### ✅ 实测结果（tools tab，webui 驱动）
1. **GGUF 量化**：`GLM-OCR-Q8_0.gguf`(907M) → q4_0，约 7s，输出同目录。
2. **vLLM→GGUF**：`/models/Qwen3-4B`(safetensors) → f32 GGUF(16.1G) → q4_k_m(2.5G)，全程约 70s。
3. **模型下载**：`Qwen/Qwen2.5-0.5B-Instruct-gguf`(ModelScope)，约 5.1G，7.4MB/s，下载至 `/models/zoo/modelscope/`。
- 测试产物已清理，保留三类输出路径可重复验证。

## v0.6.2 — 2026-08-08

### 🧰 新增独立 Tools 菜单（模型工具从 Settings 拆分）
- 顶部导航新增 **Tools** 菜单，含三个模型工具面板：
  - 📥 **模型自动下载 (Model Download)**
  - ⚗️ **GGUF 量化转换**
  - 🧬 **vLLM → GGUF 模型转换 (给 llama.cpp)**
- 对应三个 tool 面板已从 **Settings** 页移除，Settings 仅保留：服务配置 / 运维操作 / 推理引擎管理
- 切到 Tools 页时自动刷新三类任务状态（下载任务 / GGUF 转换任务 / vLLM→GGUF 转换任务）
- 文件：`frontend/index.html`、`frontend/js/app.js`

## v0.6.1 — 2026-08-08（维护）

### 🧬 Embedding 仅保留 llama_cpp 引擎（#1）
- `config/models_config.yaml` embedding `available_engines` 改为 `["llama_cpp"]`，UI 移除 vLLM 按钮
- 原因：本机无 Embedding safetensors，vLLM 加载 GGUF embedding 需手动补参，收敛为单引擎开箱即用

### ⚙️ vLLM 引擎参数缺陷修复（#3）
- **quantization auto 崩溃**：UI 默认 `quantization=auto` 被透传 `--quantization auto`，vLLM 0.22.1 报
  `Unknown quantization method: auto`。修复：值为 `auto/空/none` 时不再透传
- **Embedding 自动补参**：embedding 类别自动注入 `--dtype float16 --convert embed`，GGUF 加载无需手动改命令
- 文件：`engines/vllm.py`（备份 `vllm.py.bak_1786172301`）

### 🎨 Diffusers 引擎部署修复（#3）
- 根因：AMM server 为容器 PID1（系统 python3.11），diffusers 依赖只装了 vLLM venv，主进程无法 import
- 修复：把 numpy/diffusers/transformers/accelerate/einops/safetensors/pillow + opencv/imageio 装到系统 python
- 关键：**每次装完依赖需重启 AMM 容器**（宿主机 `docker stop/start AMM`），否则主进程缓存旧状态报
  `Numpy is not available` / `OpenCV not found`
- 实测：T2I / T2V / I2V 全部出图/出视频通过（见 docs/维护记录-20260808-引擎与diffusers.md）

## v0.6 — 2026-08-08

### 🚀 启动命令编排 (Chat/LLM/VLM llama_cpp/vllm)
- Models→Chat/LLM/VLM 新增「启动命令编排」卡片：
  - 按参数一键生成实际启动命令行（`llama-server` / `vllm` 完整参数）
  - 支持人工修改命令行，点「保存为启动脚本」写入 `scripts/<model>.sh`
  - 「清除自定义」恢复自动生成；Start/Stop/Restart 优先执行自定义脚本
- 后端：`/api/instances/{id}/command`(GET/PUT/DELETE)、`/api/instances/{id}/command/preview`(POST)
- `ModelInstance.startup_command` 持久化；`start_model` 检测到自定义命令时用 `bash scripts/<id>.sh` 启动
- 实测：chat 模型经脚本启动成功（含 `--reasoning-budget`），推理返回正常

### 🧠 llama_cpp reasoning-budget
- models_config 新增 `reasoning_budget_enabled` + `reasoning_budget`(默认8192)
- llama_cpp build_command 支持 `--reasoning-budget`，限制 Qwen3 思考 token 防空回复
- Playground Chat 默认 max_tokens 1024→16384

## v0.4 — 2026-08-07

### 📥 模型自动下载 (Task 2)
- 后端 `download_bridge`：ModelScope / HuggingFace 双源下载 API
  - `POST /api/models/download` 触发下载；`GET /api/models/download/status` 查进度
  - 独立子进程执行，状态 pending/downloading/done/failed
- WebUI Settings 新增「模型自动下载」面板，支持选择源/模型ID/类别
- 实测：ModelScope 下载 Qwen1.5-0.5B 全流程跑通

### ⚗️ GGUF 量化转换工具 (Task 3)
- 补编译 `llama-quantize`（复用已有构建树 /tmp/llama_build_cuda13）
- 后端 `quantizer.py`：`GET /api/quantize/types`、`POST /api/quantize`、`GET /api/quantize/status`
- 支持 17 种类型：f32/fp16/bf16/q4_0/q4_1/q5_0/q5_1/q8_0/q2_k/q3_k/q4_k/**q4_k_m**/q5_k/q6_k/q8_k/iq4_xs/iq3_xxs
- WebUI Settings 新增「量化转换」面板
- 实测：q4_0、q4_k_m 转换成功；fp16/bf16 需 F32/F16 源（重量化已量化模型被 llama.cpp 拒绝 - 官方规则）
- 注：当前 llama.cpp 版本无 FP8 类型（BF16=30 无 F8），fp8 需新版本 GGUF 支持

### 🎬 Diffusers T2V/I2V 验证 (Task 1)
- **I2V 完全验证通过** 🎉：Wan22-I2V-A14B 加载 90s + 推理 2分07秒(20步) + 成功导出 mp4（`generated in 167.1s, SUCCESS`）
  - I2V 加载修复：移除硬编码 `variant="bf16"`（I2V 模型无 bf16 变体文件）→ 自动检测
  - 帧导出修复：验证脚本兼容 WanPipelineOutput(.frames) + 通道规范化（对齐生产 `_extract_video_frames`）
- **T2V 已早验证**：`wan_t2v_480p_16steps_17frames_20260807-1121.mp4`（峰值 15.9G 显存，FP8+CPU offload）
- 模型完整性确认：T2V/I2V 均含 transformer/transformer_2×13 碎片 + vae + text_encoder
- 生产代码 diffusers_bridge 的 `_extract_video_frames` 已正确处理 5D→4D 帧（非临时脚本）

## v0.3 — 2026-08-07

### 🧩 Chat/LLM/VLM 运行参数完善
- **llama.cpp 参数 26 项**：上下文/GPU层/线程/batch/ubatch/FlashAttn/mlock/mmap/KV缓存类型/并行序列/温度/Top-P/Top-K/重复惩罚/窗口/Min-P/Mirostat(模式·学习率·目标困惑度)/频率与存在惩罚/RoPE缩放与基数/最大Token/种子
- **vLLM 参数 18 项**：TP/PP/DP并行/显存利用率/最大模型长度/最大并发序列/批处理Token/精度/量化(awq·gptq·fp8)/KV缓存精度(fp8)/前缀缓存/Chunked Prefill/CUDA Graph开关/KV Block/CPU卸载/对外模型名/TrustRemoteCode/种子
- 引擎 `build_command` 同步扩展（vllm.py / llama_cpp.py），参数来源：本机安装的 vLLM 0.22.1 与 llama-server 实测 --help

## v0.2 — 2026-08-07

### 🎨 WebUI 增强
- **暗/亮主题切换**：顶栏 🌙/☀️ 一键切换，选择持久化至 localStorage，全页面 CSS 变量驱动

### 🧩 模型选择（先引擎 → 路径选文件 → 参数 + 预设）
- **文件路径浏览**：Models 页新增「📂 浏览 /models」按钮，弹窗浏览容器模型目录树，按引擎过滤模型文件（llama_cpp: .gguf/.bin；vllm: .safetensors/.gguf 等），支持选整个目录（HF/Diffusers 目录型模型）
- **预设配置加载**：模型文件旁可放 `<model>.vllm` 或 `<model>.llamacpp`（YAML/JSON/key=value），WebUI 自动发现并一键加载为参数
- **参数识别**：选择模型文件后，按当前引擎展示/保存可调参数（context/GPU/采样/量化等）
- 新增 API：`/api/fs/list`、`/api/fs/discover`、`/api/instances/preset`、`/api/instances/preset/apply`、`/api/instances/preset/save`

### 💬 Playground 完善
- **模型选择下拉**：从实例中选择对话模型
- **会话参数**：Temperature / Max Tokens / Top-P / System Prompt 可调
- **多轮会话**：自动携带历史上下文，会话持久化到 localStorage
- **图片(视觉)上传**：对话中可附加图片（vision）
- **文件下载**：日志下载、生成产物内嵌预览

### ⚙️ Settings 完善
- 新增**运维操作**：重载配置、重启服务(安全重载+实例刷新)、下载服务日志
- Settings 显示版本号
- 新增 API：`/api/settings/reload`、`/api/settings/restart`

### 📋 开发产出
- 新桥接层 `backend/api/fs_bridge.py`（文件系统浏览 + 预设解析/应用/保存）
- 部署：统一入口 60006

## v0.1.1 — 2026-08-07

### ✅ 遗留问题修复
- **统一访问端口：WebUI + OpenAI 兼容 API 均走 80 → 60006**
  - 单一端口承载三者：`/`(WebUI) + `/api/*`(管理/推理) + `/v1/*`(OpenAI 兼容)
  - Web 访问：`http://<host>:60006/`；API base_url：`http://<host>:60006/v1`
  - 容器内仅剩一个监听端口 80，移除废弃的 8080/60008 映射
- **Wan2.2-T2V 冷启动验证通过** 🎉
  - 模型完整下载 118G（transformer×12 + transformer_2×12 MoE 双专家 + VAE + text_encoder + tokenizer）
  - FP8 layerwise_casting (storage=fp8, compute=bf16) + enable_model_cpu_offload 生效
  - 480p×832 / 17帧 / 16步生成成功，推理 116s，**峰值显存 15.9G**（FP8 显著降显存）
  - 输出视频已落盘 `verification/wan_t2v_480p_16steps_17frames_20260807-1121.mp4`
- **vLLM stop/restart 子进程残留修复**
  - 根因：vLLM 的 `VLLM::EngineCore` / multiprocessing resource_tracker 会 setsid 脱离父进程组，旧的 killpg 只会父进程，导致子进程残留
  - 修复：`engine.stop_process` 重构为**递归进程树清理**（psutil.children 递归，/proc 兜底），SIGTERM 后 SIGKILL 整棵回收
  - 实测：stop 后残留进程数 = 0（原会残留 EngineCore）
- **mmdc（Mermaid CLI）安装完成**：v11.16.0，复用已有 Chromium 148，思维导图渲染不再降级为纯文本

## v0.1 — 2026-08-05

### 🎯 核心能力
- **LLM 推理**: vLLM 0.22.1 (CUDA13 + Blackwell sm_120) / llama.cpp
- **文生图**: Qwen-Image-2512 (Diffusers T2I)
- **文生视频/图生视频**: Wan2.2-T2V-A14B / Wan2.2-I2V-A14B (Diffusers T2V/I2V)
- **Embedding**: Qwen3-Embedding-8B
- **多模态**: ASR/TTS/OCR (Qwen3-ASR / Qwen3-TTS / DeepSeek-OCR)
- **OpenAI 兼容 API**: `/v1/chat/completions`, `/v1/images/generations`, `/v1/videos/generations`

### 🚀 重大变更
- **FP8 量化支持** (Diffusers 0.39.0 + torch 2.11.0+cu130)
  - `enable_layerwise_casting(storage=fp8_e4m3fn, compute=bf16)` ~75G→~28G 显存
  - 自动识别 Wan2.2-A14B (MoE) 启用 FP8
  - `_skip_layerwise_casting_patterns`: patch_embedding / condition_embedder / norm
  - `_keep_in_fp32_modules`: time_embedder / scale_shift_table / norm1/2/3
- **Wan2.2 MoE 双专家** (高噪 14B + 低噪 14B)
  - boundary_ratio=0.875 (SNR=-1.5 分界)
  - guidance_scale=4.0 / guidance_scale_2=3.0
- **OpenAI /v1/videos/generations** 入口
  - `video_type=t2v|i2v` 自动路由
  - 返回 `{data:[{b64_json,mime}], saved_paths:[]}`
- **save_to_disk** 验证输出
  - 默认 `/amm/verification` (gitignored, `VERIFICATION_DIR`)
  - 命名: `<model_short>__seed<N|noseed>__<YYYYMMDD-HHMMSS>.png|mp4`

### 👨‍💻 前端
- **Playground**: 新增 T2V / I2V 子 tab
  - 分辨率 / 帧数 / steps / guidance / guidance_2 / seed
  - 视频播放预览 + 落盘提示
- **Models Detail**: diffusers 引擎下显示 **Advanced** 区块
  - quant (fp8 / bf16 / none) ⭐、compute_dtype (bf16)、boundary_ratio、cpu_offload
  - Save Advanced → 写 yaml + Restart Now 一键生效
- **主题切换**: topbar 🌙/☀️ 按钮, CSS `[data-theme="light"]`
  - localStorage 持久记忆, 全部变量重生效

### 🔄 后端 API
- `PUT /api/instances/{id}/advanced` — 写 yaml 配置 (白名单校验)
- `GET /api/instances/{id}/advanced` — 读 yaml 配置 (source of truth)

### ✅ 验证记录
| 模型 | 引擎 | 状态 | 峰值显存 | 备注 |
|------|------|------|---------|------|
| chat (Qwen2.5-14B) | vllm 0.22.1 | ✅ 已验证 | ~57G | /v1/chat/completions |
| embedding (Qwen3-E) | llama.cpp | ✅ 已验证 | ~6G | /v1/embeddings |
| asr (Qwen3-ASR) | llama.cpp | ✅ 已验证 | ~4G | /v1/audio/transcriptions |
| t2i (Qwen-Image-2512) | diffusers 0.39 | ✅ 已验证 | ~38G | 512×512 4步 136s |
| tts (Qwen3-TTS) | llama.cpp CLI | ✅ 已验证 | ~12G | /v1/audio/speech |
| t2v (Wan2.2-T2V-A14B) | diffusers 0.39 | ⏳ 下载中 | — | 58G/76G, 估计 30-50min |

### 🐛 修复
- `variant="bf16"` 硬编码 → ModelScope 权重无 `.bf16.` 后缀, 删除 variant 参数
- `_should_enable_layerwise_casting` 缺少 bf16 分支 → 补充完整决策矩阵 (bf16/fp16/fp32/none/off → False)
- AMM bridge `/v1/chat/completions` 路由 → 指向 stream handler / `_rewrite_vllm_model`
- vLLM 子进程残留 → `kill -9` 处理 (待永久修复)

### 📦 依赖
- torch 2.11.0+cu130 (Blackwell sm_120)
- diffusers 0.39.0
- accelerate 1.14.0
- vLLM 0.22.1 (CUDA13)

### ⚠️ 已知问题
- Wan2.2-T2V-A14B 仍在下载中 (58G/76G), 完整 T2V 冷启动待验证
- vLLM stop/restart 子进程残留 (VLLM::EngineCore)
- mmdc 未安装 → 思维导图渲染降级为纯文本
- RAGFlow token 未配置 → PPT 工厂降级为 memory+skill+workspace

---

## pre-v0.1 — 2026-08-04 至 08-05
- vLLM CUDA13 攻坚 + CUDA 13 铁律确立
- Qwen3-4B / Qwen-Image-2512 / Qwen3-Embedding-8B 等模型下载
- diffusers bridge 框架搭建 (T2I/T2V/I2V)
- 前端 Web UI 重构 (单页 SPA, 5 tab)
- hbppt 技能开发完成 (汇报 PPT 生成器, attached to OpenClaw)
- 友商产品采集器 ys-collector 启动

## v1.0 (2026-11-14)

### M1 地基：动态实例化
- **动态实例注册表**：同类型可并发多实例，端口池 18100-18499 自动分配（9 类固定场景 18081-18089 保留不变）
- **SQLite 持久化**：`/amm/data/amm.db`，服务重启自动恢复运行中动态实例
- **旧 API 全兼容**：`/api/instances/*` 等 v0.9 接口行为不变（36 个回归测试锁定）
- **Running Models 页面**：统计条 + Launch 表单 + 实例表格（Stop/Restart/Terminate）
- **日志中心一期**：多来源聚合、级别/关键字过滤、下载（`/api/logs/center*`）

### M2 引擎
- **sentence_transformers 引擎**（新增）：embedding/reranker，cpu/cuda 双 venv，配 `st_server.py` 子服务
- **llama.cpp 双 flavor**：`-DGGML_CUDA=OFF`(cpu) / `-DGGML_CUDA=ON`(cuda)，目录 `{version}-{flavor}` 并存
- **引擎在线替换（R8 蓝绿）**：装新版 → 起新实例 → 探活 → 切换；失败自动回滚，旧目录保留
- **引擎目录主机挂载**：`AMM_ENGINES_ROOT=/opt/amm-engines`，替换引擎不动镜像

### M3 模版
- **模版系统**：`templates/<category>/*.yaml`，8 个内置模版（chat/embedding/reranker/asr/tts/ocr/image/video）
- **Launch Model 页面**：模版卡片浏览 + 详情抽屉 + 一键部署 + 下载模型 + 另存当前实例为模版

### M4 汇聚与路由
- **外部 provider**：openai/anthropic/deepseek/icompify/自定义，key 支持环境变量引用
- **Token Router**：虚拟 key 签发（RPM/月限额/模型白名单）、路由策略（前缀强制/本地匹配/provider 权重）、
  统一 relay 转发、per-key/per-model 用量与成本统计

### M5 协议收口
- **OpenAI 全接口**：新增 `/v1/models/{id}`、`/v1/completions`、`/v1/audio/translations`、
  `/v1/images/edits`、`/v1/files*`、`/v1/batches*`
- **统一错误对象**：`{error: {message, type, param, code}}`

### 质量
- 测试：**171 个用例**，顺序两遍 + 随机顺序三遍全绿
- 用例隔离：每个用例前重置 state.json / amm.db / engines / templates / config 基线
