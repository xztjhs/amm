#!/bin/bash
# ================================================================
# AMM - 容器入口脚本 (替换 sleep infinity / tail -f /dev/null)
# ================================================================
# 职责:
#   1. 确保 SSH host key 存在并启动 sshd (远程 SSH 登录)
#   2. 启动 AMM Web 服务 (前台运行, 作为容器主进程 PID 1)
#
# 用法 (docker-compose 或 docker run):
#   command: ["/bin/bash", "/amm/deploy/docker-entrypoint.sh"]
#   (或使用 image entrypoint 指向本脚本)
# ================================================================

set -u

echo "============================================"
echo " AMM container entrypoint"
echo "============================================"

# ---------------------------------------------------------------
# [1/3] 准备环境
# ---------------------------------------------------------------
# 端口映射 (docker-compose):
#   60006 -> 80    (统一入口: WebUI 页面 + /api/* 管理 + OpenAI 兼容 /v1/*)
#                   http://host:60006 打开 WebUI;
#                   http://host:60006/v1/* 或 /api/* 为模型调用
#   60007 -> 443   (HTTPS, 预留)
#   62220 -> 22    (SSH)
echo "[init] host: $(hostname)  date: $(date '+%Y-%m-%d %H:%M:%S %Z')"

# 项目代码必须已挂载到 /amm
if [ ! -f /amm/backend/server.py ]; then
    echo "[ERROR] /amm/backend/server.py 不存在。请确认项目已挂载到 /amm (docker-compose volumes)。" >&2
    exit 1
fi

# ---------------------------------------------------------------
# [2/3] 启动 sshd (远程 SSH 登录)
# ---------------------------------------------------------------
echo "[sshd] 检查 SSH host key..."
# 仅当不存在任何 host key 时才生成（避免容器重启后 known_hosts 指纹变化）
if ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1; then
    ssh-keygen -A 2>/dev/null && echo "[sshd] 已生成缺失的 host key" || echo "[sshd] host key 生成失败(继续)"
else
    echo "[sshd] host key 已存在, 跳过生成"
fi

# 若 root 密码未设或为空, 用预设 (可选: 通过环境变量 AMM_ROOT_PASSWORD 覆盖)
if [ -n "${AMM_ROOT_PASSWORD:-}" ]; then
    echo "root:${AMM_ROOT_PASSWORD}" | chpasswd 2>/dev/null && echo "[sshd] 已设置 root 密码 (来自 AMM_ROOT_PASSWORD)"
fi

# 确保 sshd 所需目录
mkdir -p /run/sshd 2>/dev/null || true

# 若 sshd 未运行则以 daemon 方式启动; 若已在 PID1(宿主编排)则跳过
if pgrep -x sshd >/dev/null 2>&1; then
    echo "[sshd] 已在运行, 跳过启动 (PID1 编排场景)"
else
    /usr/sbin/sshd -D &
    SSHD_PID=$!
    echo "[sshd] 已启动 (pid=$SSHD_PID)"
fi

# ---------------------------------------------------------------
# [3/3] 启动 AMM Web 服务 (前台主进程)
# ---------------------------------------------------------------
echo "[AMM] 启动 Web 服务..."
cd /amm/backend || exit 1

# 首启若未安装引擎/依赖, 可在此执行安装 (默认跳过, 由镜像或首次手动完成)
if [ "${AMM_RUN_INSTALL:-0}" = "1" ]; then
    echo "[AMM] 执行 install.sh (AMM_RUN_INSTALL=1)..."
    bash /amm/deploy/install.sh --skip-gpu-check || { echo "[AMM] install.sh 失败" >&2; exit 1; }
fi

# 前台运行 AMM server, 作为容器主进程 (exit code 透传给容器)
export PYTHONPATH=/amm/backend
export AMM_ROOT=/amm
export MODELS_DIR="${MODELS_DIR:-/models}"

echo "[AMM] server listening (预期端口见 models_config.yaml: server.port)"
exec python3.11 /amm/backend/server.py