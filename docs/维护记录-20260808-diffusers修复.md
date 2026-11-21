# 维护记录 — 2026-08-08 Diffusers 反馈修复（v0.7）

> 针对 AMM 项目反馈的 4 个问题，定位并修复。涉及后端桥接、配置白名单、前端三处。

## 反馈问题与根因

### 1. Logs 里 t2i/t2v/i2v 日志一直为 empty
- **现象**：Logs 页选中 T2I/T2V/I2V，日志区恒为 `(empty)`。
- **根因**：三类模型走 diffusers **内置桥接模式**（`DiffusersEngine.build_command()` 返回 `[]` → `start_model` 分支设 `pid=0`，不生成子进程），因此 `logs/{model}_server.log` 文件**从未被创建**；而 `get_model_logs` 只读该文件 → 恒空。
- **修复**：`diffusers_bridge.py` 增加 per-model 日志写入（`log_model(model_id, ...)`），覆盖 pipeline 加载、提交任务、进入推理、完成、异常各阶段，写入 `LOGS_DIR/{model_id}_server.log`，与 Logs 页面读取路径对齐。

### 2. Playground 测 t2v/i2v 时 GPU 未工作，Dashboard 看不到工作进程
- **现象**：t2v/i2v 发起测试，GPU 利用率≈0；`nvidia-smi` 的 compute-apps 下无进程。
- **根因（关键）**：视频模型 config `cpu_offload: false` 却走到 `_load_pipeline` 的 else 分支 `enable_model_cpu_offload()` —— 序列 CPU offload，模型层被搬到 CPU、前向时才拉回 GPU，CPU 占主导、GPU 显示空闲；且桥接是 in-process，无独立进程可被 `compute-apps` 捕获。
- **修复**：
  - offload 策略**显式三态** `offload: gpu | model | group`（默认 `gpu` 全驻显存，Wan2.2-A14B FP8 ≈28G，84G 可驻）。兼容旧 `cpu_offload`（true→group，缺省/false→gpu）。
  - 后端高级设置白名单 `ADVANCED_FIELDS` 增加 `offload`，可在网页切换。
  - Dashboard 新增 `/api/bridge/diffusers/status` + 前端「Diffusers 推理任务」区块，把**进程内推理**以任务卡片方式实时展示（不再是"看不到进程"）。

### 3. Playground 发起测试 t2v/i2v 失败、拿不到最终文件
- **根因**：① 前端 `save_to_disk` 默认关闭 → 生成成功也不落盘；② 出错时仅返回错误 JSON，且日志不可查（叠加问题 1）。
- **修复**：三类模型**默认落盘** `/amm/verification`，响应带 `saved_paths` + `download_urls`；新增 `GET /api/bridge/diffusers/download?path=`（限制在 VERIFICATION_DIR，防目录穿越）；前端结果区显示「⬇ 下载文件」链接；失败写 per-model 日志可查。

### 4. 需求：记录提交→成功耗时并实时更新
- 后端任务注册记录 `submitted_at / started_at / finished_at`，计算 `elapsed_submit_to_run`、`elapsed_total`。
- 前端 Playground 三个生成入口实时显示「⏱ 计时」，完成后展示最终耗时；Dashboard 任务区块同样实时刷新。

## 涉及文件
| 文件 | 改动 |
|------|------|
| `backend/api/diffusers_bridge.py` | per-model 日志、任务状态注册、offload 策略、默认落盘、status/download 路由 |
| `backend/core/model_manager.py` | `ADVANCED_FIELDS` 增加 `offload`；类型校验 |
| `frontend/index.html` | Dashboard 加「Diffusers 推理任务」区块 |
| `frontend/js/app.js` | 实时计时、下载链接、dashboard 任务区、高级设置 offload 下拉 |
| `frontend/css/style.css` | `.diff-task-*` 样式 |

## 验证
- `python3 -m py_compile` 两个 py 通过；`node --check frontend/js/app.js` 通过；`yaml.safe_load` 通过。
- GPU 真机需在部署节点验证：video 模型 Park `offload=gpu` 后 nvidia-smi 应显示 GPU 活 && CUDA 内存占用上升；Playground 生成后 Logs/Hakiki 与 Dashboard 任务区应实时显示。

## 备注
- 默认落盘文件落在 `/amm/verification`，可通过 download 接口直接下载。
- offload 建议：显存充足（≥84G）→ `gpu`；显存紧张 → `group`。