#!/bin/bash
# ================================================================
# AMM - Diffusers 引擎安装脚本
# ================================================================
# 用法: bash install_diffusers.sh [版本号]
#   默认: 0.33.0
#   可选: 0.31.0
#
# 安装路径: /amm/backend/engines_installed/diffusers/<version>/
# 使用 Python 虚拟环境隔离安装
# ================================================================
set -e

VERSION="${1:-0.33.0}"
ENGINE_DIR="/amm/backend/engines_installed/diffusers/${VERSION}"
VENV_DIR="${ENGINE_DIR}/venv"

echo "========================================="
echo " AMM - 安装 Diffusers 引擎"
echo " 版本: ${VERSION}"
echo " 路径: ${ENGINE_DIR}"
echo "========================================="

# 依赖检查
if ! command -v python3.11 &>/dev/null; then
    echo "[ERROR] 需要 Python 3.11+"
    exit 1
fi

# 如果已安装则跳过
if [ -d "${VENV_DIR}" ] && [ -f "${VENV_DIR}/bin/python" ]; then
    if "${VENV_DIR}/bin/python" -c "import diffusers" 2>/dev/null; then
        echo "[SKIP] Diffusers ${VERSION} 已安装"
        echo "  路径: ${VENV_DIR}"
        echo "  如需重装: rm -rf ${ENGINE_DIR}"
        exit 0
    fi
fi

echo ""
echo "[1/4] 创建虚拟环境..."
python3.11 -m venv "${VENV_DIR}" --clear
PIP="${VENV_DIR}/bin/pip"
PYTHON="${VENV_DIR}/bin/python"

"${PIP}" install --upgrade pip setuptools wheel -q

echo ""
echo "[2/4] 安装 PyTorch (CUDA)..."
"${PIP}" install torch torchvision \
    --index-url https://download.pytorch.org/whl/cu121 \
    2>&1 | tail -5

echo ""
echo "[3/4] 安装 Diffusers ${VERSION} + 依赖..."
PACKAGES=(
    "diffusers==${VERSION}"
    "transformers>=4.49.0"
    "accelerate>=1.0.0"
    "safetensors>=0.4.0"
    "peft>=0.12.0"
    "modelscope>=1.21.0"
    "sentencepiece"
    "open_clip_torch"
    "imageio"
    "imageio-ffmpeg"
    "opencv-python-headless"
    "einops"
)

for pkg in "${PACKAGES[@]}"; do
    echo "  + ${pkg}"
    "${PIP}" install "${pkg}" -i https://mirrors.aliyun.com/pypi/simple/ -q 2>&1 | tail -1 || {
        echo "  [WARN] ${pkg} 安装可能有问题，继续..."
    }
done

echo ""
echo "[4/4] 配置模型缓存目录..."
# 设置环境变量，模型下载到 /models 而不是默认缓存
mkdir -p /models/huggingface /models/modelscope

# 写入 .env 文件供 AMM 使用
cat > "${ENGINE_DIR}/.env" << 'ENVEOF'
HF_HOME=/models/huggingface
HUGGINGFACE_HUB_CACHE=/models/huggingface
MODELSCOPE_CACHE=/models/modelscope
DIFFUSERS_CACHE=/models/huggingface/diffusers
TRANSFORMERS_CACHE=/models/huggingface
ENVEOF

# 验证
echo ""
echo "========================================="
echo " Diffusers 安装完成!"
echo "========================================="
"${PYTHON}" -c "
import diffusers; print(f'diffusers version: {diffusers.__version__}')
import transformers; print(f'transformers version: {transformers.__version__}')
import torch; print(f'PyTorch version: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
" 2>/dev/null || echo "[WARN] 部分包导入失败，请检查日志"

echo ""
echo "模型缓存目录:"
echo "  HuggingFace: /models/huggingface"
echo "  ModelScope:  /models/modelscope"
echo ""
echo "在 AMM Web 界面中为文生图/视频模型选择 Diffusers 引擎即可使用"
