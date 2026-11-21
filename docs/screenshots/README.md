# AMM 产品截图

本目录存放 AMM（AI Models Manage）产品功能界面截图，用于产品介绍与文档。

## 截图列表

`light-*` 系列为浅色主题截图（AMM v1.9.4，2026-11-20，分辨率 845px 宽）：

| 文件 | 板块 | 说明 |
|------|------|------|
| `light-01-dashboard.png` | Dashboard 仪表盘 | 系统状态卡片 + GPU 状态 + CPU 推理进程监控 |
| `light-02-models.png` | Models 模型管理 | 实例管理与模型配置 |
| `light-03-launch-wizard.png` | 启动向导 | 启动新实例向导（引擎/参数编排） |
| `light-04-playground-embedding.png` | Playground 推理测试 | Embedding 测试（延迟/维度/吞吐统计） |
| `light-05-token-router.png` | Token Router | Token 用量统计与路由 |
| `light-06-log-center.png` | 日志中心 | 运行日志查看与轮询降噪 |
| `light-07-settings.png` | Settings 运维设置 | 设置搜索 + 分区索引 + 全部展开 |
| `light-08-tools.png` | Tools 工具箱 | 模型下载 / GGUF 量化转换 / vLLM→GGUF 转换 |
| `light-09-templates.png` | 模版库 | 16 个内置模型模版（ASR/TTS/Embedding/Reranker/VLM 等） |

旧版深色主题截图（`01-*`～`05-*`，AMM v0.6）保留作历史参考。

## 更新方式

- 浏览器视口 845px，浅色主题（`data-theme="light"`），超长页面用 `document.documentElement.style.zoom` 缩放后截取。
- 目标地址：`http://<YOUR_HOST>:60008/`。