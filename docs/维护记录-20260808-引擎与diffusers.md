# AMM 维护记录 — 引擎加固与 Diffusers 修复（2026-08-08）

日期：2026-08-08  状态：✅ 完成并端到端验证

## 背景
对 AMM 全 9 类模型逐一做功能验证（引擎配置→启动→日志→Playground→停止）后，
发现 3 处需维护的问题，本次一并修复。

---

## #1 Embedding 仅保留 llama_cpp（移除 vLLM）

**原因**：本机没有 Embedding 的 safetensors 模型（仅 Qwen3-Embedding-8B-GGUF），
vLLM 加载 GGUF embedding 需要额外手动补参（见 #3），与「开箱即用」不符，故收敛为单引擎。

**改动**：
```
config/models_config.yaml  (authoritative: /amm/backend/config/models_config.yaml)
  embedding_model.available_engines:
    ["llama_cpp", "vllm"]  →  ["llama_cpp"]
  state.json: embedding.engine_type = "llama_cpp"
```
**验证**：Embedding 卡片仅显示 `llama_cpp ✓`，vLLM 按钮消失；chat 类保留双引擎。

---

## #3 修复 vLLM 引擎参数缺陷（quantization auto + embedding 自动补参）

**根因1（quantization 崩溃）**：UI 参数面板 quantization 默认 `"auto"`，而
`engines/vllm.py` 的 `if params.get("quantization")` 会把字面 `auto` 拼进命令行，
vLLM 0.22.1 直接报 `Unknown quantization method: auto`。
**根因2（embedding 需手动补参）**：GGUF 加载 embedding 时 vLLM 需要
`--dtype float16 --convert embed` 才能工作，否则报
`torch.bfloat16 is not supported for quantization method gguf`。

**修改** `engines/vllm.py build_command`：
- `quantization` 值为 `auto / 空 / none` 时**不再透传** `--quantization`
- embedding 类别（category=embedding 或 params.embeddings）自动注入：
  `--dtype float16`（GGUF 在 Blackwell 需 float16）+ `--convert embed`
- 备份：`engines/vllm.py.bak_1786172301`

**验证**：vllm loading embedding GGUF 自动带 `--dtype float16 --convert embed`，
`POST /v1/embeddings` 正常返回向量。

---

## #2 修复 Diffusers 引擎未部署（阻断）

**现象**：T2I `/v1/images/generations` 报 `No module named 'diffusers'`、随后报
`Numpy is not available` / `OpenCV not found`。

**根因**：AMM server 是容器 PID1，由 `/usr/bin/python3.11`（系统 python）运行，
而 `diffusers_bridge.py` 在主进程内 `import diffusers`。部署时依赖只装进了
**vLLM venv**，系统 python 缺整套 diffusers 依赖（numpy/diffusers/transformers/...）。

**修复**：把 diffusers 依赖装到 server 运行的系统 python：
```bash
/usr/bin/python3.11 -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ \
  numpy==2.3.5 diffusers==0.39.0 transformers==5.14.1 accelerate==1.14.0 \
  einops==0.8.2 safetensors==0.8.0 pillow==12.3.0
# 视频导出（OpenCV / imageio）：
  opencv-python-headless imageio imageio-ffmpeg
```
**关键经验**：**每次装完依赖必须重启 AMM 容器**，否则主进程内 torch/cv2 会缓存
「依赖不可用」的旧状态（报 `Numpy is not available` / `OpenCV not found`）。
重启方式（容器内无 systemd / docker-sock）：
```bash
# 宿主机 SSH（端口 22，非容器 62022）
ssh root@<宿主IP>
docker stop -t 10 AMM && docker start AMM   # 僵尸 PID 时先 stop 再 start
```

**实测（全部通过）**：
- **T2I** Qwen-Image-2512 → 512×512 PNG
- **T2V** Wan2.2-T2V-A14B（FP8 layerwise casting）→ 17帧 16fps 832×480 MP4
- **I2V** Wan2.2-I2V-A14B → 基于输入图生成 MP4
- 产物落盘 `/amm/verification/`

---

## 运行后状态
- 所有模型已停止，1808x 无监听，GPU 回落基线 ~17.8GB

## 备份清单（部署目录 /amm/backend 下）
- `config/models_config.yaml.bak_1786172277`
- `engines/vllm.py.bak_1786172301`

---
*记录人：技能工匠 · 2026-08-08*