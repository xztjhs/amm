#!/bin/bash
# ================================================================
# AMM 服务管理脚本 (容器内无 systemd 场景)
# ================================================================
# 用法:
#   bash /amm/deploy/amm.sh start    启动 AMM
#   bash /amm/deploy/amm.sh stop     停止 AMM
#   bash /amm/deploy/amm.sh restart  重启 AMM
#   bash /amm/deploy/amm.sh status   查看状态
#   bash /amm/deploy/amm.sh models-start  启动所有已配置模型
# ================================================================

PYTHON=/usr/bin/python3.11
AMM=/amm/backend/server.py
ROOT=/amm
LOG=/amm/logs/amm_server.out
LDPATH="/amm/backend/engines_installed/llama_cpp/b4727/bin:/usr/local/cuda-13.2/lib64"

find_amm_pid() {
  for d in /proc/[0-9]*; do
    [ -r "$d/cmdline" ] || continue
    cl=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
    case "$cl" in
      *python3.11*backend/server.py*) echo "${d#/proc/}"; return;;
    esac
  done
  echo ""
}

start() {
  pid=$(find_amm_pid)
  if [ -n "$pid" ]; then echo "AMM 已在运行 (PID $pid)"; return 1; fi
  cd "$ROOT"
  nohup env PYTHONPATH="$ROOT" AMM_ROOT="$ROOT" MODELS_DIR="/models" \
    LD_LIBRARY_PATH="$LDPATH" \
    "$PYTHON" "$AMM" > "$LOG" 2>&1 &
  echo "AMM 启动中 (PID $!)..."
  sleep 6
  curl -s -m 5 http://127.0.0.1:80/api/health && echo " <- 启动成功" || echo "启动可能失败，查看 $LOG"
}

stop() {
  for d in /proc/[0-9]*; do
    [ -r "$d/cmdline" ] || continue
    cl=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
    case "$cl" in
      *python3.11*backend/server.py*) kill "${d#/proc/}" 2>/dev/null && echo "已停止 AMM (PID ${d#/proc/})";;
    esac
  done
}

status() {
  pid=$(find_amm_pid)
  if [ -n "$pid" ]; then
    echo "AMM: 运行中 (PID $pid)"
    curl -s -m 5 http://127.0.0.1:80/api/health || echo " (health 无响应)"
  else
    echo "AMM: 未运行"
  fi
}

models_start() {
  echo "通过 API 启动所有配置的 GGUF 模型..."
  for id in chat embedding asr tts reranker ocr; do
    echo -n "  启动 $id: "
    curl -s -m 8 -X POST "http://127.0.0.1:80/api/instances/$id/start"
    echo ""
    sleep 2
  done
}

case "${1:-}" in
  start) start;;
  stop) stop;;
  restart) stop; sleep 3; start;;
  status) status;;
  models-start) models_start;;
  *) echo "用法: $0 {start|stop|restart|status|models-start}";;
esac
