#!/bin/bash
# AMM 自定义启动脚本 (人工编辑启动命令)
set -e
/usr/local/bin/llama-server -m /models/llama.cpp/Qwen3.6-35B-A3B-Uncensored/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf --host 0.0.0.0 --port 18081 --n-gpu-layers 999 --threads 16 --ctx-size 262144 --batch-size 2048 --ubatch-size 512 --flash-attn on --mlock --cache-type-k q4_0 --cache-type-v q4_0 --parallel 2 --temp 0.9 --top-p 0.9 --top-k 40 --repeat-penalty 1.1 --repeat-last-n 64 --min-p 0 --mirostat-lr 0.1 --mirostat-ent 5 --frequency-penalty 0 --presence-penalty 0 --rope-freq-scale 1 --rope-freq-base 0 --n-predict 65536 --reasoning off
