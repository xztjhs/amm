# 维护记录 — 2026-08-08 diffusers 高级参数/自动释放/Playground 三能力（v0.7.3）

> 针对 AMM diffusers（t2i/t2v/i2v）的三项增强：Models 高级参数、推理后自动释放 GPU、Playground 只读高级参数 + 显存估算。

## 一、Models 页高级参数（Quant / Compute / Offload / Boundary / CPU-Offload）
- 位置：Models → 对应模型 → 底部「🧠 Advanced (Diffusers / FP8 量化)」。
- 五项写回 `models_config.yaml` 的模型级字段，diffusers 加载时按 `_torch_dtype` / `_should_enable_layerwise_casting` 生效，Start 后按参数启动。
- 修复：`renderAdvancedSettings` 的 `defaults` 缺 `offload` 键，导致 offload 下拉不读模型实际值，已补。

## 二、每次推理后自动释放 GPU
- 实现：`t2i/t2v/i32v` 完成/异常共 6 处调用 `_maybe_release_gpu` → `del pipe`（释放局部 pipeline 引用）→ `gc.collect()` → `_pipeline_cache.clear()` → `torch.cuda.empty_cache()`。
- 实测：推理时 GPU 55.9G，完成后回落至 655M。日志 `[auto-release] keep_pipeline=否`。
- 环境变量（容器启动时传递，默认走缺省值）：

  | 变量 | 默认 | 说明 |
  |------|------|------|
  | `AMM_AUTO_RELEASE` | `full` | `full`=彻底释放+清权重（GPU 回基线）；`cache`=仅清临时碎片保留模型 |
  | `AMM_START_PRELOAD` | `0` | `1` 时 start 模型按 advanced 参数后台预加载到 GPU |
  | `AMM_AUTO_RELEASE_GPU` | `1` | `0` 关闭推理后自动释放 |

## 三、Playground 三类模型只读高级参数 + 显存估算
- T2I / T2V / I2V 三个 Playground 页签均新增：
  - 「🎛 模型高级参数（模型级·推理只读）」：Quant / Compute / Offload / Boundary / CPU-Offload 当前值。
  - 「💾 估算显存」：随请求参数（分辨率 / 帧数 / 数量 / 精度 / offload）实时变化。
- 可改的推理参数（Prompt / 尺寸 / 步数 / 引导 / 数量 / 种子）保持可改；模型级高级参数仅展示（需到 Models → Advanced → Restart）。
- 估算函数：`estimateT2iVram`（Qwen-Image-25B）、`estimateVideoVram`（Wan2.2-A14B）。

## 四、部署注意（当前未用 docker-compose）
当前容器是裸 `docker run` 启动（容器名 `AMM`，未用 compose）。若需启用预加载或调整自动释放策略，请在 `docker run -e` 传对应 `AMM_*` 环境变量，否则走默认值。

## 涉及文件
- `backend/api/diffusers_bridge.py`：`_release_gpu` / `_maybe_release_gpu`，6 处自动释放（`del pipe`）
- `backend/core/model_manager.py`：start 可选预加载（`AMM_START_PRELOAD`）
- `backend/config/models_config.yaml`：t2i / t2v / i2v 参数中英 label
- `frontend/js/app.js`：`estimateVideoVram`、`refreshPgVram` 泛化（T2I/T2V/I2V）、双语 label
- `frontend/index.html` / `frontend/css/style.css`：`pgVram-*` / `pgAdv-*` 展示块

## 验证
- JS `node --check`、py `py_compile` 通过。
- 真机 t2i 推理 200，GPU 推理后 55.9G → 655M（自动释放生效）。
- 需要环境变量的功能，补充 `AMM_*` env 并重启容器生效。