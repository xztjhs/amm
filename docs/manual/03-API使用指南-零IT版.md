# 第 3 章 AMM Web API 使用指南（零 IT 经验版）

> 面向：完全不会编程的普通使用者
> 目标：**复制粘贴**就能用上 AMM 的 AI 能力，无需懂任何技术术语。

---

## 3.0 你需要先知道的两句话

1. AMM 除了有网页界面，还开放了 **一组"电话接口"（API）**，任何能上网的工具、程序都能拨号调用。
2. 你不需要写程序。下面每一步都给你 **可以直接复制的命令**，在电脑的"终端/命令行"里粘贴回车即可。

### 怎么打开命令行？
- **Windows**：按 `Win + R`，输入 `cmd`，回车
- **Mac / Linux**：打开"终端"（Terminal）
- **手机**：跳过命令行，直接用浏览器打开验证（见 3.1 的网址方式）

### 需要什么前提？
- 能访问 AMM 服务器（浏览器能打开 `http://<服务器>:60006`）
- 对应的模型已经在 **Models 页启动**（不会？见《WebUI 操作教程》第 2.7 节）
- 你的电脑装了 `curl`（Windows 10 以上自带；Mac/Linux 都自带）

---

## 3.1 最快体验：用浏览器直接看"有哪些模型"

把下面这行**网址**复制到浏览器地址栏回车：

```
http://<服务器>:60006/v1/models
```

你会看到一堆模型清单（返回 JSON）。这就是 AMM 在说："我这里有这些模型可以用"。

> 前面都会显示 `{"object":"list","data":[{"id":"chat",...},{"id":"embedding",...}]}`。

---

## 3.2 能不能先不装任何工具就试验？

可以。用浏览器 + 一个网页工具即可，但我们推荐最通用的命令行 `curl`。
下面所有示例都用 `curl`，**直接复制 → 粘贴 → 回车** 就能用。

> 小提醒：把命令里的 `<服务器>` 换成 AMM 的 IP（如 `192.168.100.245`）。

---

## 3.3 让 AI 回答你的问题（Chat）

这是最常用的：你提问，AI 回答。

**复制下面整段，粘贴到命令行，回车：**

```bash
curl http://<服务器>:60006/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[{"role":"user","content":"你好，请用一句话介绍一下你自己"}],"max_tokens":100}'
```

**看返回**：里面有一个 `"content": "..."`，那就是 AI 的回答。

> 小知识：`model` 填 `chat` 就是对话那个模型。`content` 是你的问题，想换问题改这里即可。

---

## 3.4 想看 AI 一边想一边输出（流式对话）

网页聊天是逐字蹦出来的，API 也能这样。加一个 `"stream":true`：

```bash
curl http://<服务器>:60006/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","stream":true,"messages":[{"role":"user","content":"从1数到3"}],"max_tokens":50}'
```

输出会一段一段（`data:` 开头）不断冒出来，就像实况文字直播。

---

## 3.5 多个问题连着问（多轮对话）

聊的第二句要带上之前的对话历史：

```bash
curl http://<服务器>:60006/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[
        {"role":"user","content":"我的名字是小星"},
        {"role":"assistant","content":"你好，小星！"},
        {"role":"user","content":"我叫什么名字？"}
      ],"max_tokens":100}'
```

---

## 3.6 把一段文字变成"向量"（Embedding，用于搜索）

向量可以理解为"文字的唯一数字指纹"，做语义搜索、智能检索要用。

```bash
curl http://<服务器>:60006/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"embedding","input":"今天天气怎么样"}'
```

返回里 `data[0].embedding` 是一长串数字，那就是向量。

---

## 3.7 让 AI 认出图片里的字（OCR）

把一张图片转成文字。需要给图片一个可访问的网址，例如 `https://.../test.png`。

```bash
curl http://<服务器>:60006/v1/ocr \
  -H "Content-Type: application/json" \
  -d '{"model":"ocr","images":["这里填图片网址"],"prompt":"请识别图片中的文字"}'
```

> 前提：OCR 模型已在 Models 页启动。

---

## 3.8 把文字变成语音 / 语音变文字（TTS / ASR）

**TTS（文字 → 语音）**（无需启动常驻模型，AMM 自动调用）：

```bash
curl http://<服务器>:60006/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input":"你好，欢迎使用 AMM 语音合成。"}' \
  -o 语音.wav
```

生成的文件保存为 `语音.wav`，用播放器打开即可听到。

**ASR（语音 → 文字）**，需要一个语音文件 `语音.wav`：

```bash
curl http://<服务器>:60006/v1/audio/transcriptions \
  -F "file=@语音.wav" -F "model=asr"
```

---

## 3.9 让 AI 给一堆文档排顺序（重排序，RAG 精排）

```bash
curl http://<服务器>:60006/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"query":"今天天气","documents":["今天下雨了","明天股票大涨","我正在看书"]}'
```

返回里每个文档一个分数，分数越高越匹配。

---

## 3.10 文字生成图片（T2I）

```bash
curl http://<服务器>:60006/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"image","prompt":"一只戴帽子的小猫，卡通风格","width":1024,"height":1024,"num_inference_steps":28}'
```

> 前提：T2I 模型已启动。

---

## 3.11 文字生成视频（T2V）

```bash
curl http://<服务器>:60006/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"video","video_type":"t2v","prompt":"海浪拍打沙滩","resolution":"480p","num_frames":81,"frame_rate":16,"num_inference_steps":50}'
```

---

## 3.12 图片生成视频（I2V）

```bash
# 先把一张图片转成 base64（这里假设文件是 image.png，电脑要有 python）
python -c "import base64;print(base64.b64encode(open('image.png','rb').read()).decode())" > b64.txt

curl http://<服务器>:60006/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"video","video_type":"i2v","image":"data:image/png;base64,'$(cat b64.txt)'","prompt":"让这个画面动起来"}
'

```

> 前提：I2V 模型已启动。

---

## 3.13 出错排查（常见）

| 返回内容 | 意思 | 怎么办 |
|---------|------|--------|
| `Model xxx is not running` | 模型没启动 | 去 Models 页点 Start |
| `not running` / 503 | 同上 | 同上 |
| 网页打不开 | 服务器没通 | 检查 IP / 网络 |
| `404` | 地址写错 | 核对 /v1/xxx 大小写与斜杠 |
| 长时间无响应 | 首次加载模型慢 | 到 Logs 看进度，等一会 |

---

## 3.14 现在你能用 AMM 做什么？

你已经学会了：
- ✅ 让 AI 对话、多轮对话、流式输出
- ✅ 文字转图片、转视频、图片转视频
- ✅ 图片识文字（OCR）
- ✅ 语音转文字 / 文字转语音
- ✅ 文本变向量、给文档排序（RAG）

这些就是构建"聊天机器人 / 智能检索 / 内容生成"所需的一切底层能力。

> 想要更技术化、可编程的用法？下一章是给程序员 / AI 助手（Agent）的进阶版：
> [第 4 章 Web API 使用指南（Agent 版）](./04-API使用指南-Agent版.md)