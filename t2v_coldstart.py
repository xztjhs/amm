"""
AMM Wan2.2-T2V-A14B 冷启动验证脚本
复刻 diffusers_bridge.py 的负载逻辑:
  - WanPipeline + boundary_ratio=0.875 (MoE 双专家)
  - FP8 layerwise_casting (storage=fp8, compute=bf16)
  - cpu_offload (enable_model_cpu_offload 序列)
用法: 用装有 diffusers 0.39 的 vllm venv python 运行
  <venv>/bin/python t2v_coldstart.py [steps] [frames]
"""
import os
import sys
import time
import json
import torch

CACHE = "/models/zoo/modelscope"
os.environ["MODELSCOPE_CACHE"] = CACHE

MODEL = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
LOCAL = f"{CACHE}/models/{MODEL.replace('/', '--')}/snapshots/master"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    frames_count = int(sys.argv[2]) if len(sys.argv) > 2 else 17
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")

    # ---- 1. 加载 WanPipeline (MoE 双专家) ----
    from diffusers import WanPipeline
    # 读 model_index 确认 class
    with open(os.path.join(LOCAL, "model_index.json")) as f:
        idx = json.load(f)
    log(f"model_index class: {idx.get('_class_name')}")

    t0 = time.time()
    pipe = WanPipeline.from_pretrained(
        LOCAL,
        torch_dtype=torch.bfloat16,
        boundary_ratio=0.875,
    )
    log(f"loaded WanPipeline in {time.time()-t0:.1f}s")

    # ---- 2. FP8 layerwise_casting ----
    for attr in ("transformer", "transformer_2"):
        mod = getattr(pipe, attr, None)
        if mod is None:
            log(f"{attr}: none, skip")
            continue
        mod.enable_layerwise_casting(
            storage_dtype=torch.float8_e4m3fn,
            compute_dtype=torch.bfloat16,
        )
        log(f"layerwise_casting enabled on {attr}: storage=fp8, compute=bf16")

    # ---- 3. 显存策略: CPU offload (序列) ----
    pipe.enable_model_cpu_offload()
    log("enable_model_cpu_offload enabled")

    # ---- GPU baseline ----
    def mem():
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            total = torch.cuda.memory_reserved() / 1e9
            return used, total
        return 0, 0
    u, t = mem()
    log(f"after load+offload: allocated={u:.1f}G reserved={t:.1f}G")

    # ---- 4. 生成 ----
    from diffusers.utils import export_to_video
    prompt = "A cat walking across a sunny windowsill, golden hour light, cinematic, photorealistic"

    log(f"generating: {frames_count} frames, {steps} steps, 480p...")
    t1 = time.time()
    with torch.no_grad():
        frames = pipe(
            prompt=prompt,
            height=480, width=832,
            num_frames=frames_count,
            num_inference_steps=steps,
            guidance_scale=5.0,
            guidance_scale_2=4.0,
            output_type="np",
        )
    inp_time = time.time() - t1
    log(f"inference done in {inp_time:.1f}s")

    # diffusers 0.39: returns WanPipelineOutput with .frames (1,N,H,W,C)
    arr = getattr(frames, "frames", None)
    if arr is None and isinstance(frames, (tuple, list)):
        arr = frames[0]
    import numpy as np
    arr = np.asarray(arr)
    if arr.ndim == 5:
        arr = arr[0]
    out = export_to_video(arr, fps=16)
    log(f"saved: {out}")

    # ---- 峰值显存 ----
    peak = torch.cuda.max_memory_allocated() / 1e9
    log(f"peak GPU allocated = {peak:.1f}G")
    log(f"video saved -> {out}")

if __name__ == "__main__":
    main()
