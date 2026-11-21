#!/bin/bash
# ============================================================
# AMM - 重建 diffusers 模型 -> ModelScope cache 软链
# ------------------------------------------------------------
# 背景(2026-08-08 事故): diffusers 引擎通过 MODELSCOPE_CACHE
# (/models/zoo/modelscope) 按 ModelScope snapshot 布局定位模型,
# 模型真身保存在 /models/diffusion/ 的平铺副本中(权重完整)。
# 若 /models/zoo 被误删/清理, 会导致 t2v/i2v/t2i 报
#   "Model 本地路径未找到: ... (cache=/models/zoo/modelscope)"
# 本脚本重建 snapshot/master 软链 -> /models/diffusion/<短名>。
#
# 用法: bash ensure_diffusers_zoo_links.sh
# 幂等: 已存在则跳过。
# ============================================================
set -euo pipefail

ZOO=/models/zoo/modelscope
DIFF=/models/diffusion

# 映射: ModelScope 目录短名(org--name) -> /models/diffusion 下的模型目录
declare -A MAP=(
  ["Wan-AI--Wan2.2-T2V-A14B-Diffusers"]="Wan2.2-T2V-A14B"
  ["Wan-AI--Wan2.2-I2V-A14B-Diffusers"]="Wan2.2-I2V-A14B"
  ["Qwen--Qwen-Image-2512"]="Qwen-Image-2512"
)

mkdir -p "$ZOO/models"

for orgname in "${!MAP[@]}"; do
  short="${MAP[$orgname]}"
  src="$DIFF/$short"
  base="$ZOO/models/$orgname"
  target="$base/snapshots/master"

  if [ ! -d "$src" ]; then
    echo "[SKIP] $src 不存在(diffusion 模型目录缺失), 跳过: $short"
    continue
  fi
  if [ -e "$target" ]; then
    echo "[OK]   $target -> $(readlink "${target}" 2>/dev/null || echo 实目录)"
    continue
  fi
  mkdir -p "$base/snapshots"
  ln -s "$src" "$target"
  echo "[LINK] $target -> $src"
done

echo "DONE. 若仍报路径未找到, 请先确认对应模型已下载到 $DIFF."