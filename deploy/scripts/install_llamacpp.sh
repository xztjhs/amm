#!/bin/bash
# ================================================================
# AMM - llama.cpp 引擎安装脚本
# ================================================================
# 用法: bash install_llamacpp.sh [版本号]
#   不指定版本 = 编译最新 main 分支
#   指定版本 = 编译指定 tag (如 b4727)
#
# 安装路径: /amm/backend/engines_installed/llama_cpp/<version>/
# ================================================================
set -e

VERSION="${1:-latest}"
ENGINE_DIR="/amm/backend/engines_installed/llama_cpp/${VERSION}"
BIN_DIR="${ENGINE_DIR}/bin"
BUILD_DIR="/tmp/llama.cpp-build-${VERSION}-$$"

echo "========================================="
echo " AMM - 安装 llama.cpp 引擎"
echo " 版本: ${VERSION}"
echo " 路径: ${ENGINE_DIR}"
echo "========================================="

# 依赖检查
for cmd in git cmake gcc g++ nvcc; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "[ERROR] 缺少依赖: $cmd"
        exit 1
    fi
done

# 如果已安装则跳过
if [ -f "${BIN_DIR}/llama-server" ]; then
    echo "[SKIP] llama-server 已安装于 ${BIN_DIR}/llama-server"
    echo "  如需重新编译，请先删除: rm -rf ${ENGINE_DIR}"
    exit 0
fi

mkdir -p "${BIN_DIR}"

echo ""
echo "[1/3] 克隆 llama.cpp 源码..."

if [ "${VERSION}" = "latest" ]; then
    git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "${BUILD_DIR}"
else
    git clone --depth 1 --branch "${VERSION}" https://github.com/ggerganov/llama.cpp.git "${BUILD_DIR}" 2>/dev/null || {
        # 如果 branch 不存在则试 tag
        rm -rf "${BUILD_DIR}"
        git clone --depth 1 --branch "b${VERSION#b}" https://github.com/ggerganov/llama.cpp.git "${BUILD_DIR}" 2>/dev/null || {
            git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "${BUILD_DIR}"
            cd "${BUILD_DIR}"
            git fetch --tags
            git checkout "b${VERSION#b}" 2>/dev/null || echo "[WARN] 未找到精确版本，使用 HEAD"
        }
    }
fi

cd "${BUILD_DIR}"

echo ""
echo "[2/3] 编译 llama.cpp (CUDA)..."
cmake -B build \
    -DGGML_CUDA=ON \
    -DGGML_CUDA_F16=ON \
    -DCMAKE_CUDA_ARCHITECTURES="native" \
    -DCMAKE_BUILD_TYPE=Release

cmake --build build --config Release -j$(nproc) --target llama-server

echo ""
echo "[3/3] 安装到 ${BIN_DIR}..."


cp build/bin/llama-server "${BIN_DIR}/"
cp build/bin/llama-cli "${BIN_DIR}/" 2>/dev/null || true
cp build/bin/llama-embedding "${BIN_DIR}/" 2>/dev/null || true

# 同时安装为系统默认
cp build/bin/llama-server /usr/local/bin/llama-server
cp build/bin/llama-cli /usr/local/bin/llama-cli 2>/dev/null || true
cp build/bin/llama-embedding /usr/local/bin/llama-embedding 2>/dev/null || true

# 清理
rm -rf "${BUILD_DIR}"

# 验证
echo ""
echo "========================================="
echo " llama.cpp 安装完成!"
echo "========================================="
"${BIN_DIR}/llama-server" --version 2>&1 || true
echo ""
echo "二进制路径: ${BIN_DIR}/llama-server"
echo "系统路径:   /usr/local/bin/llama-server"
echo ""
echo "其他 llama 工具:"
ls -la "${BIN_DIR}/" 2>/dev/null
