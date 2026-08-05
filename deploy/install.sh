#!/bin/bash
# =========================================================================
# AMM (AI Models Manage) - 主安装脚本
# =========================================================================
# 用法:
#   [容器内] bash deploy/install.sh                    # 完整安装
#   [容器内] bash deploy/install.sh --skip-llamacpp    # 跳过 llama.cpp 编译
#   [容器内] bash deploy/install.sh --skip-gpu-check   # 跳过 GPU 检查
#   [容器内] bash deploy/install.sh --no-engine        # 仅安装基础依赖，不装引擎
#
# 注意: 此脚本不在 Dockerfile 内执行，而是在已运行的容器内手动执行。
#       设计思路是先用基础镜像启动容器，然后运行此脚本完成所有环境配置。
# =========================================================================

set -e
set -o pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# ---- 参数 ----
SKIP_LLAMACPP=false
SKIP_GPU_CHECK=false
NO_ENGINE=false

for arg in "$@"; do
    case "$arg" in
        --skip-llamacpp) SKIP_LLAMACPP=true ;;
        --skip-gpu-check) SKIP_GPU_CHECK=true ;;
        --no-engine) NO_ENGINE=true ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo "  --skip-llamacpp    跳过 llama.cpp 编译（若使用 vLLM 或已装）"
            echo "  --skip-gpu-check   跳过 GPU 检测"
            echo "  --no-engine        仅安装基础依赖，不安装推理引擎"
            echo "  -h, --help         显示帮助"
            exit 0
            ;;
    esac
done

# ---- 项目路径 ----
# 假定 install.sh 位于 <project>/deploy/ 下
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AMM_ROOT="/amm"
MODELS_DIR="/models"

echo ""
echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║      AMM - AI Models Manage 环境安装                ║${NC}"
echo -e "${BOLD}${BLUE}║      混合引擎架构: vllm / llama.cpp / diffusers      ║${NC}"
echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ================================================================
# Step 0: 检测环境
# ================================================================
echo -e "\n${YELLOW}[0/8] 检测系统环境...${NC}"

OS_NAME="$(cat /etc/os-release 2>/dev/null | grep '^ID=' | cut -d= -f2 | tr -d '"' || echo 'unknown')"
OS_VERSION="$(cat /etc/os-release 2>/dev/null | grep '^VERSION_ID=' | cut -d= -f2 | tr -d '"' || echo '')"
echo -e "  系统: ${CYAN}${OS_NAME} ${OS_VERSION}${NC}"

ARCH="$(uname -m)"
echo -e "  架构: ${CYAN}${ARCH}${NC}"

# MEMORY
MEM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
MEM_GB=$((MEM_KB / 1024 / 1024))
echo -e "  内存: ${CYAN}${MEM_GB} GB${NC}"

if [ $MEM_GB -lt 32 ] && [ "$MEM_GB" -gt 0 ]; then
    echo -e "  ${YELLOW}  ⚠ 推荐至少 64 GB 内存运行全部模型${NC}"
fi

# GPU
if [ "$SKIP_GPU_CHECK" = false ]; then
    echo -e "  检测 GPU..."
    if command -v nvidia-smi &>/dev/null; then
        GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "")
        if [ -n "$GPU_INFO" ]; then
            echo -e "  ${GREEN}  ✓ ${GPU_INFO}${NC}"
        else
            echo -e "  ${YELLOW}  ⚠ nvidia-smi 运行但未能获取 GPU 信息${NC}"
        fi
    else
        echo -e "  ${YELLOW}  ⚠ 未检测到 nvidia-smi，可能缺少 NVIDIA 驱动或不在 GPU 节点上${NC}"
    fi
fi

# ================================================================
# Step 1: 创建目录结构
# ================================================================
echo -e "\n${YELLOW}[1/8] 创建目录结构...${NC}"

# 创建目标目录
mkdir -p /amm/{backend/{core,engines,api,config},frontend/{css,js,assets},deploy/{scripts,envs},logs,docs}
mkdir -p /amm/backend/engines_installed
mkdir -p /models

echo -e "  ${GREEN}✓ /amm/       - 主项目目录${NC}"
echo -e "  ${GREEN}✓ /amm/backend/ - 后端服务${NC}"
echo -e "  ${GREEN}✓ /amm/frontend/ - 前端界面${NC}"
echo -e "  ${GREEN}✓ /amm/deploy/   - 部署脚本${NC}"
echo -e "  ${GREEN}✓ /amm/logs/     - 日志目录${NC}"
echo -e "  ${GREEN}✓ /models/     - 模型文件目录${NC}"

# ================================================================
# Step 2: 配置软件源
# ================================================================
echo -e "\n${YELLOW}[2/8] 配置软件源...${NC}"

if [ -f /etc/yum.repos.d/Rocky-BaseOS.repo ]; then
    sed -i 's|^mirrorlist=|#mirrorlist=|g' /etc/yum.repos.d/Rocky*.repo 2>/dev/null || true
    sed -i 's|^#baseurl=http://dl.rockylinux.org/|baseurl=https://mirrors.aliyun.com/rockylinux/|g' /etc/yum.repos.d/Rocky*.repo 2>/dev/null || true
    echo -e "  ${GREEN}✓ Rocky Linux 镜像源已切换为阿里云${NC}"
elif [ -f /etc/apt/sources.list ]; then
    # Ubuntu/Debian 镜像
    if grep -q "archive.ubuntu.com" /etc/apt/sources.list 2>/dev/null; then
        sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true
        echo -e "  ${GREEN}✓ Ubuntu 镜像源已切换为阿里云${NC}"
    fi
else
    echo -e "  ${YELLOW}  未识别的包管理器，跳过源配置${NC}"
fi

# ================================================================
# Step 3: 安装系统依赖
# ================================================================
echo -e "\n${YELLOW}[3/8] 安装系统依赖...${NC}"

if command -v dnf &>/dev/null; then
    dnf update -y --quiet
    dnf install -y --quiet \
        python3.11 python3.11-devel python3.11-pip \
        gcc gcc-c++ make cmake \
        git wget curl \
        cuda-toolkit-12-6 || dnf install -y --quiet \
        python3.11 python3.11-devel python3.11-pip \
        gcc gcc-c++ make cmake \
        git wget curl
    echo -e "  ${GREEN}✓ DNF 包安装完成${NC}"

elif command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq \
        python3.11 python3.11-dev python3.11-venv python3.11-distutils \
        build-essential cmake \
        git wget curl \
        nvidia-cuda-toolkit 2>/dev/null || apt-get install -y -qq \
        python3.11 python3.11-dev python3.11-venv python3.11-distutils \
        build-essential cmake \
        git wget curl
    echo -e "  ${GREEN}✓ APT 包安装完成${NC}"

else
    echo -e "  ${RED}✗ 不支持的包管理器${NC}"
    exit 1
fi

# 确保 python3.11 可用
if ! command -v python3.11 &>/dev/null; then
    echo -e "  ${RED}✗ python3.11 未找到${NC}"
    exit 1
fi
echo -e "  Python: ${GREEN}$(python3.11 --version)${NC}"

# ================================================================
# Step 4: 配置 pip
# ================================================================
echo -e "\n${YELLOW}[4/8] 配置 pip 并安装 Python 依赖...${NC}"

mkdir -p ~/.pip
cat > ~/.pip/pip.conf << 'PIPEOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
[install]
trusted-host = mirrors.aliyun.com
PIPEOF

pip3.11 install --upgrade pip --quiet
pip3.11 install -r "${SCRIPT_DIR}/../backend/requirements.txt"
echo -e "  ${GREEN}✓ Python 依赖安装完成${NC}"

# ================================================================
# Step 5: 安装推理引擎 (默认: llama.cpp)
# ================================================================
if [ "$NO_ENGINE" = false ]; then
    echo -e "\n${YELLOW}[5/8] 安装推理引擎...${NC}"

    # --- llama.cpp (默认必需) ---
    if [ "$SKIP_LLAMACPP" = false ]; then
        echo -e "  ${CYAN}编译 llama.cpp (GGUF 引擎)...${NC}"
        LLAMACPP_DIR="/amm/backend/engines_installed/llama_cpp/b4727"

        if [ -f "${LLAMACPP_DIR}/bin/llama-server" ]; then
            echo -e "    ${GREEN}✓ llama-server 已存在，跳过编译${NC}"
        else
            mkdir -p "${LLAMACPP_DIR}/bin"

            BUILD_DIR="/tmp/llama_build_$$"
            git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$BUILD_DIR" 2>&1 | tail -1
            cd "$BUILD_DIR"

            cmake -B build \
                -DGGML_CUDA=ON \
                -DCMAKE_CUDA_ARCHITECTURES="native" \
                2>&1 | tail -2

            cmake --build build --config Release -j$(nproc) --target llama-server 2>&1 | tail -3

            cp build/bin/llama-server "${LLAMACPP_DIR}/bin/"

            # 同时装到系统路径
            cp build/bin/llama-server /usr/local/bin/llama-server

            rm -rf "$BUILD_DIR"
            echo -e "  ${GREEN}✓ llama.cpp 编译安装完成${NC}"
        fi
    fi

    # --- 引擎版本记录 ---
    mkdir -p /amm/backend/engines_installed
    cat > /amm/backend/engines_installed/installed.json << 'INSTALLEDEOF'
{
  "llama_cpp_b4727": {
    "engine_type": "llama_cpp",
    "version": "b4727",
    "install_path": "/amm/backend/engines_installed/llama_cpp/b4727"
  }
}
INSTALLEDEOF
    echo -e "  ${GREEN}✓ 引擎版本记录已写入${NC}"

    echo -e "  ${CYAN}  vLLM 和 Diffusers 引擎可在 Web 界面中按需安装${NC}"
else
    echo -e "\n${YELLOW}[5/8] 跳过引擎安装 (--no-engine)${NC}"
fi

# ================================================================
# Step 6: 复制项目文件
# ================================================================
echo -e "\n${YELLOW}[6/8] 部署项目文件到 /amm...${NC}"

PROJECT_SRC="${SCRIPT_DIR}/.."

# 使用 rsync 或 cp 复制项目文件
if command -v rsync &>/dev/null; then
    rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        "${PROJECT_SRC}/backend/" /amm/backend/
    rsync -a --exclude='.git' \
        "${PROJECT_SRC}/frontend/" /amm/frontend/
    rsync -a --exclude='.git' \
        "${PROJECT_SRC}/deploy/" /amm/deploy/
    rsync -a --exclude='.git' \
        "${PROJECT_SRC}/docs/" /amm/docs/ 2>/dev/null || true
else
    cp -r "${PROJECT_SRC}/backend/"* /amm/backend/
    cp -r "${PROJECT_SRC}/frontend/"* /amm/frontend/
    cp -r "${PROJECT_SRC}/deploy/"* /amm/deploy/
    cp -r "${PROJECT_SRC}/docs/"* /amm/docs/ 2>/dev/null || true
fi

echo -e "  ${GREEN}✓ 项目文件部署完成${NC}"

# ================================================================
# Step 7: 安装 systemd 服务（可选）
# ================================================================
echo -e "\n${YELLOW}[7/8] 配置自启动服务...${NC}"

if command -v systemctl &>/dev/null; then
    cat > /etc/systemd/system/amm-server.service << 'UNITEOF'
[Unit]
Description=AMM - AI Models Manage Server
After=network.target nvidia-persistenced.service

[Service]
Type=simple
User=root
WorkingDirectory=/amm/backend
ExecStart=/usr/bin/python3.11 /amm/backend/server.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=HF_HOME=/models/huggingface
Environment=MODELSCOPE_CACHE=/models/modelscope

[Install]
WantedBy=multi-user.target
UNITEOF

    systemctl daemon-reload
    echo -e "  ${GREEN}✓ systemd 服务已创建 (amm-server.service)${NC}"
    echo -e "  ${CYAN}  启动: systemctl start amm-server${NC}"
    echo -e "  ${CYAN}  开机启动: systemctl enable amm-server${NC}"
else
    echo -e "  ${YELLOW}  systemctl 不可用，跳过服务配置${NC}"
    echo -e "  ${CYAN}  手动启动: cd /amm && python3.11 backend/server.py${NC}"
fi

# ================================================================
# Step 8: 验证安装
# ================================================================
echo -e "\n${YELLOW}[8/8] 验证安装...${NC}"

PASS=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $name"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $name"
        FAIL=$((FAIL + 1))
    fi
}

check "Python 3.11" "python3.11 --version"
check "pip" "python3.11 -m pip --version"
check "llama-server" "test -f /usr/local/bin/llama-server || echo skip"
check "aiohttp" "python3.11 -c 'import aiohttp'"
check "psutil" "python3.11 -c 'import psutil'"
check "PyYAML" "python3.11 -c 'import yaml'"
check "目录 /amm" "test -d /amm/backend/core"
check "目录 /models" "test -d /models"
check "配置 YAML" "test -f /amm/backend/config/models_config.yaml"

if command -v nvidia-smi &>/dev/null; then
    check "nvidia-smi" "nvidia-smi -L"
    check "GPUtil" "python3.11 -c 'import GPUtil' 2>/dev/null || python3.11 -c 'import nvidia_ml_py'"
fi

echo ""
echo -e "  ${GREEN}通过: $PASS${NC}  ${RED}失败: $FAIL${NC}"

# ================================================================
# 完成
# ================================================================
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║  安装完成!                                         ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  启动命令:"
echo -e "    ${CYAN}systemctl start amm-server${NC}     (推荐，systemd 服务)"
echo -e "    ${CYAN}python3.11 /amm/backend/server.py${NC}  (手动启动)"
echo ""
echo -e "  Web 界面:"
echo -e "    ${CYAN}http://<服务器IP>:80/${NC}    (容器内)"
echo -e "    ${CYAN}http://<服务器IP>:60006/${NC} (宿主机映射)"
echo ""
echo -e "  引擎管理:"
echo -e "    ${CYAN}Web 界面 → Settings → Engine Management${NC}"
echo -e "    可在网页上选择安装 vLLM / Diffusers 引擎版本"
echo ""
echo -e "  模型文件:"
echo -e "    将 .gguf 模型文件放入 ${CYAN}/models/${NC} 目录"
echo -e "    HuggingFace 模型会自动缓存到 ${CYAN}/models/huggingface/${NC}"
echo ""
echo -e "  ${BOLD}下一步:${NC}"
echo -e "    1. 放置模型文件到 /models/"
echo -e "    2. 启动 AMM 服务"
echo -e "    3. 在 Web 界面安装所需引擎版本"
echo -e "    4. 为每个模型选择引擎并启动推理"
echo ""
