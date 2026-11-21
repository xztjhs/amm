#!/bin/bash
# ================================================================
# AMM - Diffusers 推理服务独立启停脚本 (v0.7.1)
# ================================================================
# 与容器自启动解耦: t2i/t2v/i2v 模型懒加载, 可独立 stop(start)不重启容器。
#
# 用法:
#   bash /amm/deploy/diffusers_ctl.sh <t2i|t2v|i2v|all> start    # 预加载模型到 GPU
#   bash /amm/deploy/diffusers_ctl.sh <t2i|t2v|i2v|all> stop     # 卸载模型, 释放 GPU 显存
#   bash /amm/deploy/diffusers_ctl.sh <t2i|t2v|i2v|all> status   # 查看加载/显存状态
#   bash /amm/deploy/diffusers_ctl.sh all restart                # 卸载后全部重新加载
#
# 依赖: AMM server 已运行 (http://127.0.0.1:80)
# ================================================================

API="http://127.0.0.1:80/api/bridge/diffusers"

usage() {
    echo "用法: bash $0 <t2i|t2v|i2v|all> <start|stop|restart|status>" >&2
    exit 1
}

[ $# -lt 2 ] && usage
MODEL="$1"
ACTION="$2"

case "$MODEL" in
    t2i|t2v|i2v|all) ;;
    *) echo "model 必须是 t2i/t2v/i2v/all (收到: $MODEL)" >&2; usage ;;
esac

case "$ACTION" in
    start)
        if [ "$MODEL" = "all" ]; then
            for m in t2i t2v i2v; do
                echo "[$m] 预加载..."
                curl -s -m 1200 -X POST "$API/preload" -H "Content-Type: application/json" -d "{\"model\":\"$m\"}"
                echo
            done
        else
            echo "[$MODEL] 预加载..."
            curl -s -m 1200 -X POST "$API/preload" -H "Content-Type: application/json" -d "{\"model\":\"$MODEL\"}"
            echo
        fi
        ;;
    stop)
        echo "[$MODEL] 卸载模型, 释放显存..."
        curl -s -m 120 -X POST "$API/unload" -H "Content-Type: application/json" -d "{\"model\":\"$MODEL\"}"
        echo
        ;;
    restart)
        echo "[$MODEL] restart..."
        curl -s -m 120 -X POST "$API/unload" -H "Content-Type: application/json" -d "{\"model\":\"$MODEL\"}" >/dev/null
        if [ "$MODEL" = "all" ]; then
            for m in t2i t2v i2v; do
                echo "[$m] 重新加载..."
                curl -s -m 1200 -X POST "$API/preload" -H "Content-Type: application/json" -d "{\"model\":\"$m\"}"
                echo
            done
        else
            curl -s -m 1200 -X POST "$API/preload" -H "Content-Type: application/json" -d "{\"model\":\"$MODEL\"}"
            echo
        fi
        ;;
    status)
        echo "=== diffusers status ($(date '+%H:%M:%S')) ==="
        curl -s -m 10 "$API/status"; echo
        ;;
    *)
        usage;;
esac