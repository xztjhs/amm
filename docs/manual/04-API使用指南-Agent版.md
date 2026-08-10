# 第 4 章 AMM Web API 使用指南（面向 Agent / Skill 开发）

> 面向：OpenClaw 等 Agent、Skill 开发者、程序员
> 目标：提供可直接套用的 API 规范、请求/响应示例、鉴权说明与最佳实践，便于封装成 Skill。

---

## 4.0 总览

- **Base URL**：`http://<宿主IP>:<WEB_PORT>/`
- **无需鉴权**（当前版本无 API Key；如需安全可加装反向代理鉴权）
- **两套接口**：
  - **管理 API**：`/api/*`（模型启停、参数、引擎管理，对应 WebUI）
  - **推理 API**：`/v1/*`（OpenAI 兼容，直接面向推理）
- 响应默认 JSON；`/v1/chat/completions?stream=true` 为 SSE 流式
- 跨域：响应头 `Access-Control-Allow-Origin: *`

### 模型 id 速查表（/api/instances 与 /v1/model 用到）

| 内部 id | 类别 | 引擎 | 端口 |
|---------|------|------|:--:|
| `chat` | Chat/LLM/VLM | vllm / llama_cpp | 18081 |
| `embedding` | Embedding | llama_cpp | 18082 |
| `asr` | ASR | llama_cpp | 18083 |
| `tts` | TTS | llama_cpp | 18084 |
| `reranker` | Reranker | llama_cpp | 18085 |
| `ocr` | OCR | llama_cpp | 18086 |
| `image` | T2I | diffusers | 18087 |
| `video` | T2V / I2V | diffusers | 18088 |
| `video` | I2V | diffusers | 18089 |

> `image`/`video` 为 WebUI 显示名；`t2i`/`t2v`/`i2v` 用于 `video_type` 参数。

---

## 4.1 管理 API（/api/*）

### 4.1.1 服务健康检查
```bash
GET /api/health
```
```json
{"status":"ok","timestamp":"2026-08-09T00:20:11.717Z"}
```

### 4.1.2 系统概览
```bash
GET /api/system          # CPU/内存/磁盘/uptime/运行时长
GET /api/gpu            # 全部 GPU 详细指标
```

### 4.1.3 实例列表与详情
```bash
GET /api/instances                      # 所有实例（含参数、启动命令、日志、计数）
GET /api/instances/{model_id}           # 单个实例
```
字段要点：`status`（stopped/running）、`pid`、`gpu_memory_mb`、`engine_type`、
`selected_model_file`、`parameters`、`startup_command`、`log_lines`、`request_count`。

### 4.1.4 模型启停 / 重启
```bash
POST /api/instances/{model_id}/start
POST /api/instances/{model_id}/stop
POST /api/instances/{model_id}/restart
```

### 4.1.5 参数读写
```bash
PUT  /api/instances/{model_id}/parameters   # body = 完整参数 JSON
GET  /api/instances/{model_id}/advanced     # 读取 diffusers 高级参数
POST /api/instances/{model_id}/advanced     # 写入高级参数
PUT  /api/instances/{model_id}/engine       # 切换引擎，body: {"engine_type":"vllm"}
GET  /api/instances/{model_id}/logs?lines=100
```

### 4.1.6 启动命令编排
```bash
GET    /api/instances/{model_id}/command
POST   /api/instances/{model_id}/command/preview  # 按参数生成命令行
POST   /api/instances/{model_id}/command          # 保存自定义启动脚本
DELETE /api/instances/{model_id}/command          # 清除自定义
POST   /api/instances/{model_id}/restart          # 按脚本重启
```

### 4.1.7 模型文件 / 预设 / 配置文件
```bash
GET    /api/fs/list?path=/models&prefix=1        # 文件浏览器
GET    /api/fs/discover
POST   /api/fs/mkdir
GET    /api/config/models
GET    /api/config/models/{model_id}
GET    /api/instances/{model_id}/model-file       # 当前模型文件
POST   /api/instances/preset/save                 # 保存预设
GET    /api/instances/preset
POST   /api/instances/preset/apply
```

### 4.1.8 引擎管理
```bash
GET  /api/engines                  # 已装引擎
GET  /api/engines/versions         # 可选版本
POST /api/engines/install          # {"engine":"vllm","version":"0.8.5"}
POST /api/engines/uninstall
```

### 4.1.9 运维
```bash
POST /api/settings/reload          # 重载配置
POST /api/settings/restart         # 重启服务
GET  /api/settings                 # 服务配置(host/port/version)
GET  /api/logs/server              # 服务端日志
```

### 4.1.10 Tools：下载 / 量化 / 转换
```bash
GET  /api/models/downloads                            # 任务列表
POST /api/models/download                             # 发起下载
GET  /api/models/download/status                      # 进度
POST /api/models/download/cancel                      # 取消
POST /api/models/download/proxy                       # 保存代理
GET  /api/quantize/types                              # 量化精度清单
POST /api/quantize                                    # 发起 GGUF 量化
GET  /api/quantize/status
POST /api/convert/hf                                  # vLLM→GGUF 转换
```

### 4.1.11 Diffusers 推理桥（preload/unload 显存控制）
```bash
GET  /api/bridge/diffusers/health
GET  /api/bridge/diffusers/status                     # pipeline_cache / gpu_allocated_mb
GET  /api/bridge/diffusers/download                   # 拉取模型
POST /api/bridge/diffusers/preload                    # 预加载到 GPU(暖机)
POST /api/bridge/diffusers/unload                     # 卸载释放显存
POST /api/bridge/diffusers/t2i  |  /t2v  |  /i2v      # 生成(等价 /v1/*)
```

---

## 4.2 OpenAI 兼容 API（/v1/*）

### 4.2.1 查看模型列表
```bash
GET /v1/models
```
```json
{"object":"list","data":[
  {"id":"chat","object":"model","created":0,"owned_by":"amm"},
  {"id":"embedding","object":"model","created":0,"owned_by":"amm"},
  ...]}
```

### 4.2.2 对话（Chat Completions）
```bash
POST /v1/chat/completions
```
请求：
```json
{
  "model": "chat",
  "stream": false,
  "messages": [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "你好"}
  ],
  "max_tokens": 512,
  "temperature": 0.7
}
```
- `model`：AMM 内部 id（`chat`/`embedding`/...），或恰好与 vLLM 路径匹配时使用路径名。
- 引擎为 vLLM 时，AMM **自动把 model 重写为 vLLM 端 served 名**（如 `/models/vllm/Qwen3-4B`），调用者无需关心。
- `stream:true` 返回 `text/event-stream`（SSE），逐段 `data:` 推送，末帧 `data: [DONE]`。

### 4.2.3 向量（Embeddings）
```bash
POST /v1/embeddings   {"model":"embedding","input":"今天天气"}
```
返回 `data[].embedding`（数字数组）。支持 `input` 为字符串或数组。

### 4.2.4 语音识别（ASR）
```bash
POST /v1/audio/transcriptions
```
multipart：`file=@音频`、`model=asr`。透传 llama-server 结果。

### 4.2.5 语音合成（TTS）
```bash
POST /v1/audio/speech
```
```json
{"input":"你好","model":"tts"}
```
返回 `audio/wav` 二进制。AMM 直接调用 llama-tts CLI 生成（无需常驻进程）。
可选 `tts_lang`（zh/en/...），从 TTS 模型参数读取。

### 4.2.6 OCR
```bash
POST /v1/ocr
```
```json
{"model":"ocr","images":["https://.../1.png"],"prompt":"识别图中文字","max_tokens":2048,"temperature":0.2}
```
内部转为 chat 视觉多模态请求，返回与 chat/completions 同构。

### 4.2.7 重排序（Rerank）
```bash
POST /v1/rerank
```
```json
{"query":"今天天气","documents":["今天下雨","明天晴天"]}
```
转发 llama-server `/rerank`，返回每个 doc 的 relevance score。

### 4.2.8 文生图（T2I）
```bash
POST /v1/images/generations
```
```json
{"model":"image","prompt":"一只猫","width":1024,"height":1024,
 "num_inference_steps":28,"guidance_scale":5.0,"seed":-1,"n":1,"scheduler":"flow_match_euler"}
```
返回含生成图片的路径/URL 或 base64（`saved_paths`）。

### 4.2.9 文生视频 / 图生视频
```bash
POST /v1/videos/generations
```
**T2V**：
```json
{"model":"video","video_type":"t2v","prompt":"海浪","resolution":"480p",
 "num_frames":81,"frame_rate":16,"num_inference_steps":50,"guidance_scale":5.0,"seed":-1}
```
**I2V**：多一个 `image`（base64，可带 `data:image/png;base64,` 前缀）
```json
{"model":"video","video_type":"i2v","image":"<base64>","prompt":"动起来",
 "resolution":"480p","num_frames":81,"frame_rate":16,"num_inference_steps":50}
```
返回含 `saved_paths`（生成文件路径）与视频二进制。

> 参数默认值汇总：num_inference_steps=50(video)/28(image)、guidance_scale=5.0、frames=81、fps=16、resolution=480p。

---

## 4.3 在 OpenClaw / Agent 中调用（最佳实践）

### 4.3.1 用 web_fetch（只读 GET）
`/v1/models`、`/api/health`、`/api/instances` 等 GET 接口可直接用本网关的 `web_fetch` 或 curl 拉取。

### 4.3.2 用 exec curl（POST 推理）
需要 POST 且有 JSON 的推理调用，用 `exec` 执行如下 curl：

```bash
curl -s http://<IP>:<WEB_PORT>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[{"role":"user","content":"<prompt>"}]}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

### 4.3.3 流式调用（SSE）
```bash
curl -sN http://<IP>:<WEB_PORT>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","stream":true,"messages":[{"role":"user","content":"hi"}]}'
```

### 4.3.4 封装成 AMM Skill 的建议接口面
把 AMM 能力暴露给 Agent 的 Skill 建议提供以下动作：
- `list_models()` → GET /v1/models
- `chat(message, model="chat", stream=false)` → POST /v1/chat/completions
- `embed(text)` → POST /v1/embeddings
- `ocr(image_url)` → POST /v1/ocr
- `tts(text, lang)` → POST /v1/audio/speech（存 wav）
- `rerank(query, docs)` → POST /v1/rerank
- `image(prompt)` / `video(prompt, video_type, image?)` → /v1/images|videos/generations
- `start_model(id)` / `stop_model(id)` → 管理 API
- 统一封装返回可读文本/文件路径，错误处理（模型未启动 → 提示先 start）。

### 4.3.5 错误处理惯例
| HTTP | 含义 | Agent 处理 |
|:--:|------|-----------|
| 200 | 成功 | 解析返回 |
| 404 | 模型 id 不存在 | 校验 /v1/models 中存在的 id |
| 503 | 模型未运行 | 调 `start_model(id)` 后重试 |
| 504 | 超时 | 增大 timeout，或确认模型在加载 |

---

## 4.4 完整 Skill 示例骨架（Python / aiohttp 调用）

```python
import aiohttp

BASE = "http://<YOUR_SERVER_IP>:<WEB_PORT>"

async def amm_chat(user_text: str, **kw) -> str:
    body = {"model": kw.get("model", "chat"),
            "messages": [{"role": "user", "content": user_text}],
            "max_tokens": kw.get("max_tokens", 1024)}
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/v1/chat/completions", json=body) as r:
            data = await r.json()
    return data["choices"][0]["message"]["content"]

# 用前先确保模型启动：POST /api/instances/{id}/start
```

---

下一章：[第 5 章 功能详细·组件架构](./05-功能设计组件架构.md)