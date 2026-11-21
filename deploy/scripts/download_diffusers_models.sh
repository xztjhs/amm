#!/bin/bash
# ============================================================
# AMM Diffusers 模型下载脚本 (ModelScope 优先, 国内源)
# 下载: Qwen-Image-2512 (T2I) + Wan2.2-T2V/I2V-A14B-Diffusers (视频)
# 使用 vLLM venv 的 python (已装 modelscope)
# ============================================================
set -e
PYBIN=/amm/backend/engines_installed/vllm/0.22.1/venv/bin/python
CACHE=/models/zoo/modelscope
LOG=/amm/logs/download_models.log
export MODELSCOPE_CACHE=$CACHE
export MODELSCOPE_DOMAIN=modelscope.cn

echo ">>> [$(date)] Start model downloads -> $CACHE" | tee -a $LOG
mkdir -p $CACHE

$PYBIN - <<'EOF' 2>&1 | tee -a $LOG
import os
from modelscope import snapshot_download
cache = "/models/zoo/modelscope"
os.environ.setdefault("MODELSCOPE_CACHE", cache)

models = [
    ("Qwen/Qwen-Image-2512", "T2I"),
    ("Wan-AI/Wan2.2-T2V-A14B-Diffusers", "T2V"),
    ("Wan-AI/Wan2.2-I2V-A14B-Diffusers", "I2V"),
]
for mid, tag in models:
    print(f"*** [{tag}] downloading {mid} ***", flush=True)
    try:
        p = snapshot_download(mid, cache_dir=cache)
        print(f"[{tag}] OK -> {p}", flush=True)
    except Exception as e:
        print(f"[{tag}] FAIL: {e}", flush=True)
print("ALL DONE", flush=True)
EOF
echo ">>> [$(date)] downloads finished" | tee -a $LOG
