#!/bin/bash
# ============================================================
# AMM Diffusers 环境初始化脚本 (rev3)
# 策略: 直接在 vLLM 已验证的 venv 里安装 diffusers 全家桶.
#       该 venv 已有 torch 2.11+cu130 (Blackwell sm_120 验证通过),
#       diffusers/transformers/accelerate 与 vllm 无冲突, 共存即可.
# 规则: 国内镜像 + ModelScope 优先
# ============================================================
set -e

VENV=/amm/backend/engines_installed/vllm/0.22.1/venv
PYBIN=$VENV/bin/python
PIP="$PYBIN -m pip"
LOG=/amm/logs/setup_diffusers.log

echo ">>> [$(date)] Start diffusers setup (rev3: reuse vllm venv torch cu130)" | tee -a $LOG

# 1. pip 镜像 + 升级
$PIP install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -1 | tee -a $LOG

# 2. 安装 diffusers 全家桶 (只装 torch 之外的库, torch 已在)
echo ">>> Installing diffusers family (no torch reinstall)" | tee -a $LOG
$PIP install \
  "diffusers>=0.33.0" \
  "transformers>=4.49.0" \
  "accelerate>=1.0.0" \
  "safetensors>=0.4.0" \
  "peft>=0.12.0" \
  "modelscope>=1.39.0" \
  "numpy" \
  "pillow" \
  "imageio" \
  "imageio-ffmpeg" \
  "ftfy" \
  "open-clip-torch" \
  "datasets" \
  -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -8 | tee -a $LOG
echo ">>> pip family done" | tee -a $LOG

# 3. 验证 (注意: 不 import vllm 以免初始化触发显存分配)
$PYBIN - <<'EOF' 2>&1 | tee -a $LOG
import torch, diffusers, transformers, modelscope
print("torch", torch.__version__, "| cuda", torch.cuda.is_available(),
      "| cap", torch.cuda.get_device_capability() if torch.cuda.is_available() else "n/a")
print("diffusers", diffusers.__version__)
from diffusers import QwenImagePipeline, WanPipeline, AutoModel
print("QwenImagePipeline / WanPipeline / AutoModel import OK")
EOF

echo ">>> [$(date)] env setup DONE" | tee -a $LOG
