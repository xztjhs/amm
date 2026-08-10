#!/bin/bash
PY=/amm/backend/engines_installed/vllm/0.22.1/venv/bin/python
LOG=/amm/logs/dl_all.log
{
echo "===== [$(date)] T2I Qwen-Image-2512 ====="
$PY /amm/dl_one.py Qwen/Qwen-Image-2512
echo "===== [$(date)] T2V Wan ====="
$PY /amm/dl_one.py Wan-AI/Wan2.2-T2V-A14B-Diffusers
echo "===== [$(date)] I2V Wan ====="
$PY /amm/dl_one.py Wan-AI/Wan2.2-I2V-A14B-Diffusers
echo "ALLDONE"
} >> $LOG 2>&1
