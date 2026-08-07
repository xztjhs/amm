from norm_frames import normalize_frames
"""
AMM Diffusers 单测脚本 - 验证 pipeline 能否加载 + 生成
用法: python verify_diffusers.py <t2i|t2v|i2v> [gpu|offload]
"""
import os
import sys
import time
import torch

CACHE = "/models/zoo/modelscope"
os.environ["MODELSCOPE_CACHE"] = CACHE

def resolve(model_id):
    org_name = model_id.replace("/", "--")
    p = f"{CACHE}/models/{org_name}/snapshots/master"
    if os.path.isdir(p):
        return p
    p2 = f"{CACHE}/{org_name}"
    if os.path.isdir(p2):
        return p2
    raise FileNotFoundError(p)

def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "t2i"
    mode = sys.argv[2] if len(sys.argv) > 2 else "offload"

    t0 = time.time()
    if task == "t2i":
        from diffusers import QwenImagePipeline
        local = resolve("Qwen/Qwen-Image-2512")
        print("local:", local)
        pipe = QwenImagePipeline.from_pretrained(local, torch_dtype=torch.bfloat16, variant="bf16")
    else:
        import json
        from diffusers import WanPipeline, WanImageToVideoPipeline
        if task == "t2v":
            local = resolve("Wan-AI/Wan2.2-T2V-A14B-Diffusers")
            cls = WanPipeline
        else:
            local = resolve("Wan-AI/Wan2.2-I2V-A14B-Diffusers")
            with open(os.path.join(local, "model_index.json")) as f:
                idx = json.load(f)
            cn = idx.get("_class_name", "")
            cls = {"WanImageToVideoPipeline": WanImageToVideoPipeline}.get(cn, WanPipeline)
            print("model_index class:", cn, "->", cls.__name__)
        print("local:", local)
        # 自动检测 bf16 变体是否可用，避免 I2V(无 variant 文件) 加载失败
        import os as _os
        has_bf16 = _os.path.exists(_os.path.join(local, "transformer", "diffusion_pytorch_model-00001-of-00012-bf16.safetensors")) or                    any(x.endswith("-bf16.safetensors") for x in _os.listdir(_os.path.join(local, "transformer"))) if _os.path.isdir(_os.path.join(local, "transformer")) else False
        kw = dict(torch_dtype=torch.bfloat16)
        if has_bf16:
            kw["variant"] = "bf16"
        pipe = cls.from_pretrained(local, **kw)

    print(f"loaded in {time.time()-t0:.1f}s")

    if mode == "offload":
        pipe.enable_model_cpu_offload()
        print("cpu offload enabled")
    else:
        pipe = pipe.to("cuda")
        print("cuda mode")

    print("generating...")
    t1 = time.time()
    with torch.no_grad():
        if task == "t2i":
            imgs = pipe(
                prompt="A red panda sitting on a bamboo branch, warm sunlight, photorealistic",
                height=768, width=768,
                num_inference_steps=20,
                num_images_per_prompt=1,
            )
            img = imgs.images[0]
            out = "/amm/logs/verify_t2i.png"
            img.save(out)
            print("saved", out)
        elif task == "t2v":
            from diffusers.utils import export_to_video
            frames = pipe(
                prompt="A cat walking across a sunny windowsill",
                height=480, width=832, num_frames=17,
                num_inference_steps=20, guidance_scale=5.0, output_type="np",
            )
            # 兼容 pipeline 返回结构：元组的[0]可能是帧tensor/flist/str路径
            # Wan pipeline 返回 WanPipelineOutput(.frames)；兼容 list/tuple/str
            if hasattr(frames, "frames"):
                vid = frames.frames
            elif isinstance(frames, (tuple, list)):
                vid = frames[0]
            else:
                vid = frames
            if isinstance(vid, str):
                import glob
                cands = glob.glob("/amm/verification/*.mp4") + glob.glob("/amm/logs/*.mp4")
                print("frames is path str:", vid[:60], "| existing:", cands[-1:] if cands else "none")
                out = cands[-1] if cands else vid
            else:
                out = normalize_frames(vid); print("normalized frames ->", out)
            print("saved", out)
        else:  # i2v
            from diffusers.utils import export_to_video
            import base64, io
            from PIL import Image
            # 用 t2i 结果或占位图
            img_path = "/amm/logs/verify_t2i.png"
            if os.path.exists(img_path):
                image = Image.open(img_path).convert("RGB")
            else:
                image = Image.new("RGB", (832, 480), (120, 180, 200))
            frames = pipe(
                image=image, prompt="the scene comes alive, subtle motion, clouds drift",
                height=480, width=832, num_frames=17,
                num_inference_steps=20, guidance_scale=5.0, output_type="np",
            )
            # 兼容 pipeline 返回结构：元组的[0]可能是帧tensor/flist/str路径
            # Wan pipeline 返回 WanPipelineOutput(.frames)；兼容 list/tuple/str
            if hasattr(frames, "frames"):
                vid = frames.frames
            elif isinstance(frames, (tuple, list)):
                vid = frames[0]
            else:
                vid = frames
            if isinstance(vid, str):
                import glob
                cands = glob.glob("/amm/verification/*.mp4") + glob.glob("/amm/logs/*.mp4")
                print("frames is path str:", vid[:60], "| existing:", cands[-1:] if cands else "none")
                out = cands[-1] if cands else vid
            else:
                out = normalize_frames(vid); print("normalized frames ->", out)
            print("saved", out)
    print(f"generated in {time.time()-t1:.1f}s")
    print("SUCCESS")

if __name__ == "__main__":
    main()
