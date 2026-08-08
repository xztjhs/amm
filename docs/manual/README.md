# AMM 用户手册（v0.8.0）

> **AMM（AI Models Manage）· AI 模型管理与推理框架**
> 统一管理 9 类 AI 模型，混合推理引擎（llama.cpp / vLLM / Diffusers），Web 网页端全流程配置与测试。
> 文档版本：v0.8.0 ｜ 更新日期：2026-08-09
> 统一入口：`http://<宿主IP>:60006/`

---

## 手册目录

| 章 | 内容 | 面向人群 |
|----|------|---------|
| 01 | [AMM 产品及功能介绍](./01-产品与功能简介.md) | 所有人 |
| 02 | [Web UI 全部功能与操作步骤（step by step 教程）](./02-WebUI操作教程.md) | 运维/使用者 |
| 03 | [WEB API 使用指南（零 IT 经验版）](./03-API使用指南-零IT版.md) | 普通用户 |
| 04 | [WEB API 调用方法（Agent/Skill 版）](./04-API使用指南-Agent版.md) | OpenClaw 等 Agent 开发 |
| 05 | [功能设计·组件·架构](./05-架构与组件设计.md) | 开发/架构 |
| 06 | [开发测试问题记录与修复方法](./06-问题记录与修复.md) | 维护/开发 |
| 070 | 功能截图（浅色） | 附见各章引用 |

> 手册代码库：`docs/manual/` ｜ 截图：`docs/screenshots/`

---

## 快速上手（30 秒）

**第 1 步** 浏览器打开 `http://<服务器IP>:60006/`（回车即进入，无登录）。

**第 2 步** 进入 **Models** 页，选择要使用的模型类别（如 Chat/LLM/VLM），确认引擎与模型文件后点击 **Start** 启动。

**第 3 步** 进入 **Playground** 页，选择刚启动的模型，输入问题点 **Send** 即可对话/测试。

> 详细步骤见 [第 2 章 WebUI 操作教程](./02-WebUI操作教程.md)。
> 调用 API 见 [第 3 章（零IT版）](./03-API使用指南-零IT版.md)。

---

## 当前已安装引擎与模型一览（历史环境）

| 引擎 | 已装版本 | 支持的模型类别 |
|------|---------|---------------|
| llama.cpp | b4727 | chat / embedding / asr / tts / reranker / ocr |
| vLLM | 0.8.5（另有 0.22.1 / 0.7.3 可选） | chat / embedding |
| Diffusers | 0.39.0（另有 0.33.0 可选） | t2i / t2v / i2v |

| 模型实例 | 引擎 | 端口 | 状态默认 |
|---------|------|:--:|:--:|
| Chat/LLM/VLM | vLLM（Qwen3-4B） | 18081 | stopped |
| Embedding | llama.cpp | 18082 | stopped |
| ASR | llama.cpp | 18083 | stopped |
| TTS | llama.cpp | 18084 | stopped |
| Reranker | llama.cpp | 18085 | stopped |
| OCR | llama.cpp | 18086 | stopped |
| T2I | Diffusers | 18087 | stopped |
| T2V | Diffusers | 18088 | running（演示） |
| I2V | Diffusers | 18089 | stopped |