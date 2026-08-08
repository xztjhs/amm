# Diffusers 文生图/视频引擎 - 部署与验证记录

日期: 2026-08-05  状态: 进行中

## 背景
AMM 项目补齐 Diffusers 引擎，支持文生图(T2I)/文生视频(T2V)/图生视频(I2V)。

## 硬件/环境
- 验证机: 10.10.10.10 (SSH 62022, root/CHANGE_ME_PASSWORD)
- GPU: RTX 6000D 84GB / CUDA 13.2 / Blackwell sm_120
- 依赖环境: **vLLM 0.22.1 venv** (`/amm/backend/engines_installed/vllm/0.22.1/venv`)
  - torch 2.11.0+cu130 (Blackwell 验证通过)
  - diffusers 0.39.0, transformers 5.14.1, accelerate 1.14.0, modelscope 1.39.1
  - 另补: GPUtil, nvidia-ml-py (server 依赖)

## 关键决策点
1. **清华 pip 源 403** → 改用阿里云镜像 `mirrors.aliyun.com/pypi/simple/`
2. **diffusers 装在 vLLM venv** 而非系统 python → **server.py 必须用 vLLM venv python 启动**，否则 bridge 无法 import diffusers
3. **Wan 必须用 `-Diffusers` 转换版** (原始版 `Wan2.2-T2V-A14B` diffusers 加载不了)
4. **Qwen-Image 用 `QwenImagePipeline`** (不是通用 `DiffusionPipeline`)

## 模型 (ModelScope, cache=/models/zoo/modelscope)
| 用途 | 模型 | pipeline | 大小 |
|---|---|---|---|
| T2I | Qwen/Qwen-Image-2512 | QwenImagePipeline | ~15GB |
| T2V | Wan-AI/Wan2.2-T2V-A14B-Diffusers | WanPipeline | ~28GB |
| I2V | Wan-AI/Wan2.2-I2V-A14B-Diffusers | WanImageToVideoPipeline | ~28GB |

## 部署切换 (需停 vLLM 释放显存后操作)
```bash
# 1. 停 vLLM 实例 (释放 57GB)
#    通过 AMM API 或 kill

# 2. 停旧 server (系统 python)
#    kill <server_pid>

# 3. 用 vLLM venv python 启动 server
nohup /amm/backend/engines_installed/vllm/0.22.1/venv/bin/python /amm/backend/server.py \
  > /amm/logs/amm_server.log 2>&1 &

# 4. 验证
#    单测: /amm/verify_diffusers.py t2i/t2v/i2v
#    API:  curl -X POST http://127.0.0.1:8080/v1/images/generations -d '{"prompt":"..."}'
```

## 2026-08-08 修复记录（v0.6.1）
**当前有效方案**：AMM 容器内 server 是 PID1（`/usr/bin/python3.11`，systemd/docker-sock 均不可用），
故不是切换 venv，而是把 **diffusers 依赖直接装到系统 python**，与“server 用 venv python 启动”等效：
```bash
/usr/bin/python3.11 -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ \
  numpy==2.3.5 diffusers==0.39.0 transformers==5.14.1 accelerate==1.14.0 \
  einops==0.8.2 safetensors==0.8.0 pillow==12.3.0 \
  opencv-python-headless imageio imageio-ffmpeg
```
**关键**：每次装完依赖需重启 AMM 容器（宿主机 SSH 端口 22：`docker stop -t 10 AMM && docker start AMM`），
否则主进程 torch/cv2 缓存旧状态（报 `Numpy is not available` / `OpenCV not found`）。

实测 T2I(Qwen-Image-2512)/T2V/I2V(Wan2.2-A14B FP8) 全部出图/出视频通过。
详见 `docs/维护记录-20260808-引擎与diffusers.md`。


## 待办
- [ ] 等模型下载完成
- [ ] 单测 verify_diffusers (需停 vLLM)
- [ ] 切换 server 到 venv python
- [ ] API 端到端验证 t2i/t2v/i2v
- [ ] 恢复 vLLM chat 服务
