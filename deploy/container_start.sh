#!/bin/bash
# ================================================================
# AMM - 容器启动脚本
# ================================================================
# 用法:
#   1. docker compose -f deploy/docker-compose.yml up -d
#   2. docker exec -it amm-server bash
#   3. bash /amm/deploy/container_start.sh
#
# 或一次性启动:
#   docker compose -f deploy/docker-compose.yml up -d
#   docker exec amm-server bash /amm/deploy/container_start.sh
# ================================================================

echo "========================================="
echo " AMM - 容器启动配置"
echo "========================================="

# 运行安装脚本（容器内首次运行）
if [ ! -f /amm/backend/server.py ]; then
    echo "[WARN] 项目代码未挂载到 /amm，请检查 docker-compose volumes 配置"
    exit 1
fi

echo "[1/2] 安装依赖和引擎..."
bash /amm/deploy/install.sh --skip-gpu-check

echo ""
echo "[2/2] 启动 AMM 服务..."
cd /amm/backend
PYTHONPATH=/amm/backend exec python3.11 /amm/backend/server.py
