# 维护记录 — 2026-08-08 diffusers 高级参数/自动释放/Playground 三能力（v0.7.3）

> 针对 AMM diffusers（t2i/t2v/i2v）的三项增强 + 两处修复：Models 高级参数、推理后自动释放 GPU、Playground 只读高级参数+显存估算、Advanced 区不显示 bug、compose 环境变量。

## 一、Models 页高级参数（Quant / Compute / Offload / Boundary / CPU-Offload）
- 位置：Models → 对应模型 → 底部「🧠 Advanced (Diffusers / FP8 量化)」。
- 五项写回 `models_config.yaml` 的模型级字段，diffusers 加载时按 `_torch_dtype` / `_should_enable_layerwise_casting` 生效，Start 后按参数启动。
- 修复：`renderAdvancedSettings` 的 `defaults` 缺 `offload` 键，导致 offload 下拉不读模型实际值，已补。

## 二、每次推理后自动释放 GPU
- 实现：`t2i/t2v/i2v` 完成/异常共 6 处调用 `_maybe_release_gpu` → `del pipe`（释放局部 pipeline 引用）→ `gc.collect()` → `_pipeline_cache.clear()` → `torch.cuda.empty_cache()`。
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
当前容器是裸 `docker run` 启动（容器名 `AMM`，未用 compose）。若需启用预加载或调整自动释放策略，请在 `docker run -e` 传对应 `AMM_*` 环境变量，否则走默认值。`deploy/docker-compose.yml` 已补齐这些变量，用 compose 启动时自带：

  | 变量 | 默认 | 说明 |
  |------|------|------|
  | `AMM_AUTO_RELEASE` | `full` | `full`=彻底释放+清权重（GPU 回基线）；`cache`=仅清临时碎片保留模型 |
  | `AMM_START_PRELOAD` | `0` | `1` 时 start 模型按 advanced 参数后台预加载到 GPU |
  | `AMM_AUTO_RELEASE_GPU` | `1` | `0` 关闭推理后自动释放 |
  | `AMM_ROOT` / `MODELS_DIR` / `PYTHONPATH` | `/amm` 等 | 容器路径（compose environment 已设） |

## 五、Models 页 Advanced 区不显示 bug 修复（commit 6cafbc7）
- **根因**：`renderAdvancedSettings` 是 async 函数，但在同步模板 `${...?renderAdvancedSettings(id):''}` 直接求值 → 拿到 Promise → 渲染空。
- **修复**：模板改为 `<div id="adv-wrap-{id}">` 占位，渲染后异步注入；t2i/t2v/i2v 三模型 Advanced 均正常。

## 六、环境变量详细说明（AMM_AUTO_RELEASE / AMM_START_PRELOAD / AMM_AUTO_RELEASE_GPU）

> 这三个变量都作用于 **diffusers 推理（T2I/T2V/I2V）**，在容器 `environment`（docker run -e 或 compose）设置，**启动时一次性读取，改后需重启容器生效**。

### 6.1 `AMM_AUTO_RELEASE_GPU` —— 总开关（默认 `1`）
- **功能**：开关。设为 `0/false/off/no` 时**完全关闭"推理结束后自动释放 GPU"这一整套机制**；其余值（含缺省）开启。
- **用途**：一键决定"每次推理后要不要自动清理 GPU 显存"。
- **代码逻辑**（`diffusers_bridge.py`）：
  ```python
  AUTO_RELEASE_GPU = os.environ.get("AMM_AUTO_RELEASE_GPU", "1") not in ("0","false","off","no")
  # _maybe_release_gpu():
  if not AUTO_RELEASE_GPU:   # 关着 → 直接 return，什么都不做
      return
  ```
- **推荐**：默认保持 `1`。除非你能精确手动管理显存（手动 unload / 想最大化连续推理速度避免反复重载），否则开启最省心。

### 6.2 `AMM_AUTO_RELEASE` —— 释放策略（默认 `full`）
- **特点**：设定每次推理结束后 GPU 释放到什么程度，只在 `AMM_AUTO_RELEASE_GPU=1` 时生效。
- **两个取值**：
  | 值 | 行为 | 推理后 GPU 占用 | 代价 |
  |------|------|------|------|
  | **`full`**（默认） | 彻底释放：`del pipe` → `gc.collect` → **清空 `_pipeline_cache`（卸载权重）** → `torch.cuda.empty_cache()` | 回落基线（实测 **55.9G → 655M**） | 下次推理需**重新加载模型**（t2i 冷加载 ~2-3min，t2v/i2v 更久） |
  | **`cache`** | 仅 `gc` + `empty_cache()`，**保留已加载 pipeline（权重驻留）** | 仍保留 ~50G+ 权重 | 下次推理快（无需重载） |
- **代码逻辑**：
  ```python
  mode = os.environ.get("AMM_AUTO_RELEASE", "full")
  _release_gpu(model_id=..., keep_pipeline=(mode != "full"))
  # keep_pipeline=False → pipeline_cache.clear() + empty_cache → 彻底卸载权重
  # keep_pipeline=True  → 仅 empty_cache → 保留模型
  ```
- **关键点**：在这台 85G 卡上，**`full` 才是"释放显存"的真正意义**——`cache` 模型虽然快但模型仍占 ~50G，等于没真正释放（正是之前"GPU 长期占 50-85G" 的痛点）。实测证明：仅 `cache` 时推理完还是 55.9G，`full` 才掉回 655M。
- **推荐做法（分场景）**：
  - **测试/偶发使用**（当前场景）→ `full`：推理完彻底释放，不长期占用显存 ✅
  - **生产/高频连续生成** → `cache`，或干脆 `AMM_AUTO_RELEASE_GPU=0` 常驻提速（前提显存富余且不跑其他模型）
  - 保持 `full` 需要提速时，用 `diffusers_ctl.sh all preload` 或手动触发预加载 warm-up

### 6.3 `AMM_START_PRELOAD` —— start 是否预加载（默认 `0`）
- **特点**：点 Models 页 **Start 启动 diffusers 模型**时，是否**立刻按当前 Advanced 参数后台把 pipeline 预加载进 GPU**。
  - `0`（默认）：Start 只标记 running，不真正加载；首个推理请求时才懒加载。
  - `1`：Start 即触发 `_preload_diffusers` 后台任务 → 标 running 时模型已就位，首次推理无冷启动。
- **代码逻辑**（`model_manager.py`）：
  ```python
  if os.environ.get("AMM_START_PRELOAD","0") in ("1","true","on") and ...diffusers...:
      asyncio.create_task(self._preload_diffusers(model_cfg))
  ```
- **重要警告（为何默认 0）**：**别轻易开 1**。之前实测——start 预加载会把 t2i 吃到 ~50G+，紧接着第一次推理再叠加激活 → **CUDA OOM**（"预加载驻留 + 推理激活"超显存总量；自动释放是推理后才触发，救不了 preload 阶段）。
- **推荐**：
  - 默认保持 `0`（懒加载最稳，不会因预加载占满显存而 OOM）。
  - 仅两种场景考虑开 `1`：①明确当前无其他模型占用 GPU 且显存足够；②想消除首次请求冷启动延迟且能接受 start 后显存被占。
  - 需要 warm-up 更安全的方式：保持 `0`，需要时 `bash /amm/deploy/diffusers_ctl.sh t2i start`（按需预加载、用完可 stop）。

### 6.4 三者组合与推荐配置
| 场景 | 推荐配置 |
|---|---|
| **测试验证（现状）** | `AMM_AUTO_RELEASE_GPU=1` + `AMM_AUTO_RELEASE=full` + `AMM_START_PRELOAD=0` ✅（compose 即此组） |
| **高频连续生成** | `AMM_AUTO_RELEASE_GPU=0`（全关释放）或 `AMM_AUTO_RELEASE=cache`; `AMM_START_PRELOAD` 视显存 |
| **显存紧/多模型切换** | `AMM_AUTO_RELEASE=full`（释放最彻底）+ `AMM_START_PRELOAD=0` |

**一句话**:`full`=真正释放显存（代价下次冷加载）；`cache`/`0`=保显存但快；`START_PRELOAD=0` 安全默认（防 preload+推理叠加 OOM）。

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