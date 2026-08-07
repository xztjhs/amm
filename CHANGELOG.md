# AMM Changelog

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
