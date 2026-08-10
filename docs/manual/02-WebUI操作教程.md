# 第 2 章 AMM WebUI 操作教程（Step by Step）

> 版本：v0.8.0 ｜ 面向：运维人员 / 普通使用者
> 环境：`http://<宿主IP>:<WEB_PORT>/`（无需登录）

---

## 2.0 顶部导航总览

进入 AMM 后，顶部为导航栏，共 6 大板块：

```
[Dashboard] [Models] [Playground] [Logs] [Tools] [Settings]   [刷新间隔▼] [🌙/☀️] Online 00:00:00
```

- 右侧「刷新间隔」下拉：可选 0.1s ~ 5min，控制 Dashboard/Logs 自动刷新频率
- 「🌙/☀️」：暗色 / 浅色主题切换（localStorage 持久）
- 「Online HH:MM:SS」：实时在线时长

---

## 2.1 Dashboard（仪表盘）

**位置**：左侧第一个菜单。

**功能**：系统与模型整体状态总览。

### Step 1 查看系统概览卡片
顶部 4 张卡片，每 0.5s 自动刷新：
- **CPU**：当前使用率
- **Memory**：已用 / 总内存（GB）
- **Disk**：已用 / 总磁盘（GB）
- **Uptime**：运行时长

### Step 2 查看 GPU 状态
「GPU 状态」区域展示全部 GPU 的多列指标：
- 型号 / GPU 编号
- 计算利用率、显存带宽、编码器、解码器
- **显存占用**（如 `3/85651 MB`）
- 温度、风扇转速、功耗（如 `36W / 450W`）
- SM 时钟、显存时钟、PCIe 代数
- 「正在运行的程序」列表

### Step 3 查看 Model Status（模型状态）
9 类模型卡片，每张显示：
- 图标 + 名称 + 实例 id
- 状态：`RUNNING`（绿） / `STOPPED`（灰）
- 引擎类型、端口、GPU 显存、CPU、Uptime
- 操作按钮：**Start / Stop / Restart**

> 点击任一模型卡片可跳到 Models 页对应模型。

📷 截图：`docs/screenshots/light-01-dashboard.png`

---

## 2.2 Models（模型配置与管理）

**位置**：Models。

**功能**：为 9 类模型选择引擎、配置模型文件与参数、启动/停止/重启。
这是 AMM 最核心的页面。

### Step 1 选择模型类别
自上而下依次是：Chat/LLM/VLM、Embedding、ASR、TTS、Reranker、OCR、T2I、T2V、I2V。
每个区块独立，互不影响。

### Step 2 选择推理引擎
每类模型下方有「⚙️ 推理引擎」按钮组，已选引擎带 `✓`：
- Chat：`llama_cpp` / `vllm`
- 其余 llama 类：`llama_cpp`
- 图像类：`diffusers`

点击即可切换（需重启动模型生效）。

### Step 3 选择模型文件
点击「📂 浏览 /models」打开文件浏览器，选 .gguf / .safetensors / 目录。
选定后显示路径，如：
- Chat: `vllm/Qwen3-4B`
- Embedding: `Qwen/Qwen3-Embedding-8B-GGUF/Qwen3-Embedding-8B-Q4_K_M.gguf`
- T2V: `diffusion/Wan2.2-TI2V-5B`

### Step 4 编辑参数并保存
- Chat（vLLM）：TP/PP/DP 并行数、GPU 显存利用率、最大长度、量化方式、KV 缓存精度、前缀缓存、Trust Remote Code 等
- llama_cpp 类：上下文长度、GPU 层数、线程数、批大小、Flash Attention、内存锁定等
- Diffusers：分辨率/尺寸、帧数、步数、引导强度、seed、scheduler 等
- 改完点 **Save Parameters** 持久化。

### Step 5 高级参数（Diffusers）
LLM/Diffusers 高级参数区（v0.7.3 增强）：
- **Quant 存储精度**：fp8（省显存，推荐）/ bf16 / none
- **Compute Dtype**：bf16 / fp16 / fp32
- **Boundary Ratio**：Wan2.2 MoE 双专家切换点（SNR）
- **CPU Offload** 开关 + **Offload 策略**：gpu（全驻显存）/ model（序列 offload）/ group（叶子 offload）
- 改完点 **Save Advanced**，再 **🔄 Restart Now** 生效。

### Step 6 启动命令编排（🚀）
- 「⚙️ 生成启动命令」：根据参数自动生成 `llama-server` / `vllm` 完整命令行
- 「💾 保存为启动脚本」：写入 `scripts/<model>.sh`，Start 时直接执行
- 「🗑 清除自定义」「🔄 Restart (执行脚本)」
- 适合给模型加自定义参数（如 `--reasoning-budget`）

### Step 7 启动 / 停止 / 重启
- 点 **Start** 开始推理（vLLM 首次加载模型约 1-4 分钟）
- 点 **Stop** 停止并释放显存
- 点 **Restart** 重启
- T2V 等 Diffusers 模型：Start/Stop 独立启停，配合 `diffusers_ctl.sh`

> 📷 截图：`docs/screenshots/light-02-models.png`、`02b-models-chat-config.png`

---

## 2.3 Playground（推理测试区）

**位置**：Playground。

**功能**：网页内直接测试各种模型的推理效果，也是"对没经验的 IT"演示最佳入口。

### 2.3.1 Chat（对话）
1. 顶部切换到「💬 Chat」
2. 下拉「模型」选择已启动的模型（如 `chat — vllm · Qwen3-4B (运行中)`）
3. 可调 Temperature / Max Tokens / Top-P，可填 System 提示
4. 底部输入框输入内容，点 **Send**
5. 输出流式显示；「🗑 清空会话」重来

### 2.3.2 Embedding（向量）
- 输入文本，返回向量数据

### 2.3.3 ASR / TTS（语音）
- ASR：上传音频 → 转文字
- TTS：输入文字 → 生成语音

### 2.3.4 Rerank / OCR
- Reranker：输入 query + 文档列表 → 相关性排序
- OCR：上传图片 → 识别文字

### 2.3.5 T2I（文生图）
- 输入描述 prompt，选宽高/步数/seed/count/scheduler
- 生成图片，显示显存估算（v0.7.3 新增：权重 + 激活 + 固定）

### 2.3.6 T2V（文生视频）/ I2V（图生视频）
- T2V：prompt → 视频（选分辨率 480p/720p、帧数、帧率、步数）
- I2V：上传图片 + prompt → 视频

> 📷 截图：`docs/screenshots/light-03-playground-chat.png`

---

## 2.4 Logs（日志）

**位置**：Logs。

**功能**：实时查看各模型运行日志，排查问题从这开始。

**用法**：
1. 顶部下拉选择模型（CHAT / EMBEDDING / ASR / TTS / RERANKER / OCR / T2I / T2V / I2V）
2. 行数（默认 100）可调
3. 勾选 **Auto** —— 自动跟随模型日志实时滚动
4. 点 **Refresh** 手动刷新

> 日志服务端存储于 `/amm/logs/<model>_server.log`。

> 📷 截图：`docs/screenshots/light-08-logs.png`

---

## 2.5 Tools（模型工具）

**位置**：Tools。

### 2.5.1 模型自动下载
- 从 **ModelScope（国内优先）/ HuggingFace** 下载
- 支持：HTTP 代理、断点续传、速度/剩余时间显示、版本选择
- **目标目录（v0.7）**：可选填相对 /models 的路径（如 `vllm/Qwen3-4B`），下载直接落到该目录，与引擎路径一致
- 步骤：填模型 ID → 「🔍 查询版本与文件」→ 「⬇️ 开始下载」→ 「🔄 刷新任务」查看进度

### 2.5.2 GGUF 量化转换
- 源 GGUF → 按精度转换（q4_k_m 推荐 / q8_0 / fp16 / bf16 等）
- 选源文件 → 精度 → 输出目录 → 「⚗️ 开始转换」

### 2.5.3 vLLM → GGUF 模型转换
- 把 Safetensors 模型转成 GGUF 给 llama.cpp
- 源模型 → 量化精度 → 输出目录 → 模型名 → 「🧬 转换」

> 📷 截图：`docs/screenshots/light-06-tools.png`

---

## 2.6 Settings（设置）

**位置**：Settings。

### 2.6.1 服务配置
- API Host / Port（0.0.0.0:80）
- Models Dir / Logs Dir
- **版本号**（当前 v0.8.0）

### 2.6.2 运维操作
- **🔄 重载配置**：热重载 models_config
- **🔄 重启服务**：重启 AMM 主服务
- **📥 下载服务日志**：导出日志

### 2.6.3 引擎管理
按引擎列出版本，网页**安装 / 卸载**：
- llama.cpp：`b4727（安装）` / `latest`
- vLLM：`0.22.1 / 0.8.5 / 0.7.3`
- Diffusers：`0.39.0（安装）` / `0.33.0`
安装完成后在 Models 页即可为新版切换。

> 📷 截图：`docs/screenshots/light-07-settings.png`

---

## 2.7 完整操作示例：从启动到对话（5 分钟）

如果你对这个系统不熟悉，照着做一遍即可上手：

1. 打开 `http://<宿主IP>:<WEB_PORT>/`
2. 进 **Models**，拉到 **Chat / LLM / VLM** 卡片
3. 确认引擎为 vllm（若没有点 `vllm`），模型文件为 `vllm/Qwen3-4B`
4. 点 **Start**，等待日志里出现 "Application startup complete"（约 1-4 分钟）
5. 进 **Playground** → **💬 Chat** → 模型下拉选 `chat — vllm · Qwen3-4B (运行中)`
6. 输入"你好，你是做什么的？" → **Send**
7. 看到回复即成功 ✅

> 去下一个：想用 API？见 [第 3 章](./03-API使用指南-零IT版.md)。

---

## 2.8 常见疑惑

| 现象 | 原因 / 处理 |
|------|-----------|
| Start 后长时间没起来 | 看看是 vLLM 首次加载，去 Logs 看该模型日志确认 |
| Playground 显示"模型未启动" | 先到 Models 把该模型 Start，再回 Playground 选择 |
| 内存/显存占用很高 | Diffusers 推理内存自动释放开关见 Settings/Advanced；可点 Stop 释放 |
| 主题想改 | 顶部 🌙/☀️ 切换 |