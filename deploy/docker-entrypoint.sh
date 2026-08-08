#!/bin/bash
# ================================================================
# AMM - 容器入口脚本 (supervisor 模式, v0.7.1)
# ================================================================
# 职责:
#   1. 确保 SSH host key 存在并启动 sshd (远程 SSH 登录)
#   2. 以 "supervisor" 方式启动 AMM Web 服务 (作为后台子进程)
#   3. server.py 不再是 PID 1 -> kill 掉 server 仅触发自动重启, 容器不死
#   4. t2i/t2v/i2v 等 diffusers 推理与 Web 主服务解耦 (懒加载 + 独立启停),
#      不再随容器自启动常驻 GPU 显存
#
# 用法 (docker-compose 或 docker run):
#   command: ["/bin/bash", "/amm/deploy/docker-entrypoint.sh"]
#
# 生命周期:
#   - kill <server_pid>            -> supervisor 检测到退出, 自动重启 server
#   - docker stop / SIGTERM       -> 转发信号并优雅退出
#   - diffusers 启停:
#       bash /amm/deploy/diffusers_ctl.sh t2v stop   # 卸载模型, 释放 GPU 显存
#       bash /amm/deploy/diffusers_ctl.sh t2v start  # 预加载模型
# ================================================================

set -u

echo "============================================"
echo " AMM container entrypoint (supervisor)"
echo "============================================"

# ---------------------------------------------------------------
# [1/4] 准备环境
# ---------------------------------------------------------------
# 端口映射 (docker-compose):
#   60006 -> 80    (统一入口: WebUI + /api/* 管理 + OpenAI 兼容 /v1/*)
#   62220 -> 22    (SSH)
echo "[init] host: $(hostname)  date: $(date '+%Y-%m-%d %H:%M:%S %Z')"

# 项目代码必须已挂载到 /amm
if [ ! -f /amm/backend/server.py ]; then
    echo "[ERROR] /amm/backend/server.py 不存在。请确认项目已挂载到 /amm (docker-compose volumes)。" >&2
    exit 1
fi

# ---------------------------------------------------------------
# [2/4] 启动 sshd (远程 SSH 登录)
# ---------------------------------------------------------------
echo "[sshd] 检查 SSH host key..."
if ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1; then
    ssh-keygen -A 2>/dev/null && echo "[sshd] 已生成缺失的 host key" || echo "[sshd] host key 生成失败(继续)"
else
    echo "[sshd] host key 已存在, 跳过生成"
fi

if [ -n "${AMM_ROOT_PASSWORD:-}" ]; then
    echo "root:${AMM_ROOT_PASSWORD}" | chpasswd 2>/dev/null && echo "[sshd] 已设置 root 密码 (来自 AMM_ROOT_PASSWORD)"
fi
mkdir -p /run/sshd 2>/dev/null || true

if pgrep -x sshd >/dev/null 2>&1; then
    echo "[sshd] 已在运行, 跳过启动 (supervisor 编排场景)"
else
    /usr/sbin/sshd -D &
    SSHD_PID=$!
    echo "[sshd] 已启动 (pid=$SSHD_PID)"
fi

# ---------------------------------------------------------------
# [3/4] AMM server 启停辅助 (supervisor)
# ---------------------------------------------------------------
AMM_DIR=/amm/backend
AMM_SRV=/amm/backend/server.py
AMM_OUT=/amm/logs/amm_server.out
AMM_PID=0

# server 崩溃后自动重启的间隔 (秒)
AMM_RESTART_DELAY="${AMM_RESTART_DELAY:-3}"

start_amm() {
    cd "$AMM_DIR" || return 1
    export PYTHONPATH=/amm/backend
    export AMM_ROOT=/amm
    export MODELS_DIR="${MODELS_DIR:-/models}"
    echo "[AMM] 启动 server..."
    nohup python3.11 "$AMM_SRV" >> "$AMM_OUT" 2>&1 &
    AMM_PID=$!
    echo "[AMM] server started pid=$AMM_PID (out=$AMM_OUT)"
}

stop_amm() {
    if [ "$AMM_PID" -gt 0 ]; then
        echo "[AMM] 发送 SIGTERM 到 server pid=$AMM_PID"
        kill -TERM "$AMM_PID" 2>/dev/null
        # 给 20s 优雅退出
        for _ in $(seq 1 20); do
            kill -0 "$AMM_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$AMM_PID" 2>/dev/null
        wait "$AMM_PID" 2>/dev/null
        AMM_PID=0
    fi
}

# ------------------------------------------------------------
# [4/4] supervisor 主循环
#   PID 1 = 本 wrapper。server 作为子进程; 被杀/崩溃 -> 自动重启。
# ------------------------------------------------------------
handle_signal() {
    echo "[AMM] 收到信号 ($1), 优雅停止..."
    stop_amm
    echo "[AMM] 已退出"
    exit 0
}
trap 'handle_signal TERM' TERM
trap 'handle_signal INT' INT
trap 'handle_signal HUP' HUP

if [ "${AMM_RUN_INSTALL:-0}" = "1" ]; then
    echo "[AMM] 执行 install.sh (AMM_RUN_INSTALL=1)..."
    bash /amm/deploy/install.sh --skip-gpu-check || { echo "[AMM] install.sh 失败" >&2; exit 1; }
fi

# 首次启动
start_amm

echo "============================================"
echo " AMM supervisor 就绪. server_pid=$AMM_PID"
echo "  容器内重启:  kill -TERM $AMM_PID"
echo "  diffusers 启停: bash /amm/deploy/diffusers_ctl.sh <t2i|t2v|i2v|all> <start|stop|status>"
echo "============================================"

# 主循环: wait server 退出后自动重启 (除非收到 exit 标志)
AMM_EXITING=0
while :; do
    if [ "$AMM_PID" -le 0 ]; then
        echo "[AMM] server 未运行, 重启..."
        start_amm
        continue
    fi

    # wait 返回时 server 已退出 (被 kill 或崩溃)
    wait "$AMM_PID"
    RC=$?
    # 停留标记: 若存在 .amm_stop 则真正退出 (供 diffusers stop 全停场景)
    if [ -f /amm/.amm_stop ]; then
        echo "[AMM] 检测到 /amm/.amm_stop, 停止 server 且不自动重启"
        rm -f /amm/.amm_stop
        exit 0
    fi
    echo "[AMM] server 退出 rc=$RC, ${AMM_RESTART_DELAY}s 后重启..."
    sleep "$AMM_RESTART_DELAY"
    start_amm
done