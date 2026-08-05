#!/bin/bash
# ================================================================
# AMM - vLLM 引擎安装脚本
# ================================================================
# 用法: bash install_vllm.sh [版本号]
#   默认: 0.8.5
#   可选: 0.7.3
#
# 安装路径: /amm/backend/engines_installed/vllm/<version>/
# 使用 Python 虚拟环境隔离安装
# ================================================================
set -e

VERSION="${1:-0.8.5}"
ENGINE_DIR="/amm/backend/engines_installed/vllm/${VERSION}"
VENV_DIR="${ENGINE_DIR}/venv"

echo "========================================="
echo " AMM - 安装 vLLM 引擎"
echo " 版本: ${VERSION}"
echo " 路径: ${ENGINE_DIR}"
echo "========================================="

# 依赖检查
if ! command -v python3.11 &>/dev/null; then
    echo "[ERROR] 需要 Python 3.11+"
    exit 1
fi

# CUDA 检查
if ! command -v nvcc &>/dev/null; then
    echo "[WARN] 未检测到 nvcc，将继续安装但可能无法 GPU 推理"
fi

# 检查显存
GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | grep -oP '\d+' || echo 0)
if [ "$GPU_MEM" -gt 0 ] && [ "$GPU_MEM" -lt 16000 ]; then
    echo "[WARN] GPU 显存 ${GPU_MEM} MB < 推荐 16000 MB，可能无法运行大模型"
fi

# 如果已安装则跳过
if [ -f "${VENV_DIR}/bin/vllm" ]; then
    echo "[SKIP] vLLM ${VERSION} 已安装"
    echo "  路径: ${VENV_DIR}/bin/vllm"
    echo "  如需重装: rm -rf ${ENGINE_DIR}"
    exit 0
fi

echo ""
echo "[1/3] 创建虚拟环境..."
python3.11 -m venv "${VENV_DIR}" --clear
PIP="${VENV_DIR}/bin/pip"
PYTHON="${VENV_DIR}/bin/python"

# 升级 pip
"${PIP}" install --upgrade pip setuptools wheel -q

echo ""
echo "[2/3] 安装 PyTorch (CUDA)..."
"${PIP}" install torch torchvision \
    --index-url https://download.pytorch.org/whl/cu121 \
    2>&1 | tail -5

echo ""
echo "[3/3] 安装 vLLM ${VERSION}..."
"${PIP}" install "vllm==${VERSION}" \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    2>&1 | tail -5

# 验证
echo ""
echo "========================================="
echo " vLLM 安装完成!"
echo "========================================="
"${PYTHON}" -c "import vllm; print(f'vLLM version: {vllm.__version__}')"
echo ""
echo "使用方式:"
echo "  ${VENV_DIR}/bin/python -m vllm.entrypoints.openai.api_server --model <model_path>"
echo ""
echo "或在 AMM Web 界面中选择 vLLM 引擎后启动模型"
