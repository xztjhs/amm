import os
import sys

CACHE = "/models/zoo/modelscope"
MDL = sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen-Image-2512"
os.environ["MODELSCOPE_CACHE"] = CACHE
os.environ["MODELSCOPE_DOMAIN"] = "modelscope.cn"
from modelscope import snapshot_download

print("downloading", MDL, flush=True)
p = snapshot_download(MDL, cache_dir=CACHE)
print("OK", p, flush=True)
