"""诊断 WanPipeline 输出结构 (diffusers 0.39)"""
import os, time, json, torch
CACHE = "/models/zoo/modelscope"
os.environ["MODELSCOPE_CACHE"] = CACHE
MODEL = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
LOCAL = f"{CACHE}/models/{MODEL.replace('/', '--')}/snapshots/master"

from diffusers import WanPipeline
pipe = WanPipeline.from_pretrained(LOCAL, torch_dtype=torch.bfloat16, boundary_ratio=0.875)
for a in ("transformer","transformer_2"):
    m=getattr(pipe,a,None)
    if m: m.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
pipe.enable_model_cpu_offload()

with torch.no_grad():
    out = pipe(prompt="a cat", height=480, width=832, num_frames=17,
               num_inference_steps=1, guidance_scale=5.0,
               guidance_scale_2=4.0, output_type="np")
print("TYPE:", type(out))
print("DIR:", [x for x in dir(out) if not x.startswith('_')])
if hasattr(out,"frames"):
    f=out.frames
    print("frames attr type:", type(f), "len:", len(f) if hasattr(f,'__len__') else 'NA')
    if hasattr(f,'__len__') and len(f):
        e0=f[0]
        print("frame[0] type:", type(e0))
else:
    print("no .frames attr")
    print("iter test:", type(iter(out)) if hasattr(out,'__iter__') else 'not iterable')
