# AMM 产品截图

本目录存放 AMM（AI Models Manage）产品功能界面截图，用于产品介绍与文档。

## 截图列表

| 文件 | 板块 | 说明 |
|------|------|------|
| `01-dashboard.png` | Dashboard 仪表盘 | 系统状态卡片 + GPU 状态多列 + 模型运行状态 |
| `02-models.png` | Models 模型管理 | 9 类模型配置与管理（引擎选择、参数编辑） |
| `02b-models-chat-config.png` | Models—Chat/LLM/VLM | 对话模型展开：引擎切换 + 参数配置 + 启动命令编排 |
| `03-playground-chat.png` | Playground 推理测试 | Chat 对话测试（流式 / 性能统计） |
| `04-logs.png` | Logs 日志 | 运行日志查看 |
| `05-settings.png` | Settings 运维 | 运维设置（重载/重启/下载/引擎/模型下载/量化） |

截图分辨率 3200px 宽（2× 高分屏），适用于产品介绍、PPT、文档配图。

## 更新方式

- 截图由 `python3 /tmp/amm_capture*.py`（playwright + 本机 chromium）对 `http://<YOUR_SERVER_IP>:<WEB_PORT>/` 各板块自动截取。
- 界面版本：AMM v0.6（2026-08-08）。