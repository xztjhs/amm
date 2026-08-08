# AMM Changelog

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
   - 开启后下载（HF/ModelScope）经代理转发；测试代理 `http://192.168.100.73:7891`。
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
