# -*- coding: utf-8 -*-
"""
AMM 下载辅助脚本（由 download_bridge.py 以子进程方式调用）。
通过环境变量接收参数，规避 format 转义问题。

OP:
  revisions  - 查询模型版本/分支 + 每版本文件清单与大小
  download   - 执行下载（代理/版本/文件子集/断点续传 + 进度 JSON 心跳）

环境变量:
  AMM_OP, AMM_SOURCE, AMM_MODEL, AMM_CACHE, AMM_REVISION,
  AMM_FILES, AMM_PROXY, AMM_PROGRESS, AMM_TOTAL
"""
import os
import sys
import json
import time
import threading
import traceback
from pathlib import Path

OP = os.environ.get("AMM_OP", "")
source = os.environ.get("AMM_SOURCE", "huggingface")
model_id = os.environ.get("AMM_MODEL", "")
cache = os.environ.get("AMM_CACHE", "/models/zoo/huggingface")
revision = os.environ.get("AMM_REVISION", "") or None
files = json.loads(os.environ.get("AMM_FILES", "[]") or "[]")
proxy = json.loads(os.environ.get("AMM_PROXY", "{}") or "{}")
progress_file = os.environ.get("AMM_PROGRESS", "")
total_ref = int(os.environ.get("AMM_TOTAL", "0") or 0)


def apply_proxy():
    if proxy.get("enabled") and proxy.get("url"):
        u = proxy["url"]
        os.environ["HTTP_PROXY"] = u
        os.environ["HTTPS_PROXY"] = u
        os.environ["ALL_PROXY"] = u
        os.environ["NO_PROXY"] = "localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,100.0.0.0/8"


apply_proxy()
os.environ["MODELSCOPE_CACHE"] = "/models/zoo/modelscope"
os.environ["MODELSCOPE_DOMAIN"] = "modelscope.cn"
os.environ["HF_HOME"] = "/models/huggingface"
# 禁用 HF Xet CDN 协议(经 HTTP 代理常不可达/不稳定), 回退传统分块下载, 代理兼容性更好
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


# =====================================================================
# 版本/文件清单查询
# =====================================================================
def op_revisions():
    out = {"versions": [], "error": ""}
    try:
        if source == "huggingface":
            from huggingface_hub import HfApi
            api = HfApi()
            refs = api.list_repo_refs(model_id)
            entries = []
            for r in (getattr(refs, "branches", None) or []):
                entries.append({"revision": r.name, "type": "branch"})
            for r in (getattr(refs, "tags", None) or []):
                entries.append({"revision": r.name, "type": "tag"})
            if not entries:
                entries.append({"revision": "main", "type": "main"})
            for e in entries:
                try:
                    info = api.model_info(model_id, revision=e["revision"])
                    siblings = sorted({s.rfilename for s in info.siblings}, key=str.lower)
                    sizes = {}
                    try:
                        paths = api.get_paths_info(model_id, revision=e["revision"],
                                                   paths=siblings)
                        for item in (paths or []):
                            sizes[item.path] = item.size
                    except Exception:
                        pass
                    flist = [{"filename": f, "size": sizes.get(f)} for f in siblings]
                    e["files"] = flist
                    e["total_size"] = sum((f["size"] or 0) for f in flist)
                except Exception as ex:
                    e["files"] = []
                    e["total_size"] = 0
                    e["error"] = str(ex)[-200:]
            out["versions"] = entries
        else:
            from modelscope.hub.api import HubApi
            api = HubApi()
            branches = api.get_model_branches_and_tags(model_id)
            br = branches.get("branches", []) if isinstance(branches, dict) else []
            tg = branches.get("tags", []) if isinstance(branches, dict) else []
            entries = [{"revision": b, "type": "branch"} for b in br]
            entries += [{"revision": t, "type": "tag"} for t in tg]
            if not entries:
                entries.append({"revision": "master", "type": "main"})
            for e in entries:
                try:
                    flist = api.get_model_files(model_id, revision=e["revision"])
                    fs = []
                    for f in flist or []:
                        if isinstance(f, dict):
                            fn = f.get("Path") or f.get("Name")
                            sz = f.get("Size")
                        else:
                            fn = getattr(f, "Path", None) or getattr(f, "Name", None)
                            sz = getattr(f, "Size", None)
                        if not fn:
                            continue
                        try:
                            sz = int(sz) if sz else None
                        except Exception:
                            sz = None
                        fs.append({"filename": str(fn), "size": sz})
                    e["files"] = fs
                    e["total_size"] = sum((f["size"] or 0) for f in fs)
                except Exception as ex:
                    e["files"] = []
                    e["total_size"] = 0
                    e["error"] = str(ex)[-200:]
            out["versions"] = entries
    except Exception as e:
        out["error"] = str(e)
        out["trace"] = traceback.format_exc()[-800:]
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if not out.get("error") else 1)


# =====================================================================
# 下载执行（含进度统计）
# =====================================================================
def cache_size_bytes():
    total = 0
    try:
        root = Path(cache)
        if not root.exists():
            return 0
        for p in root.rglob("*"):
            try:
                if p.is_file() and ".download-progress" not in str(p):
                    total += p.stat().st_size
            except Exception:
                pass
    except Exception:
        pass
    return total


_state = {
    "done": 0, "total": total_ref, "speed": 0.0, "eta": None,
    "cur": model_id, "nfiles": 0, "ndone": 0, "detail": "",
}
_stop = threading.Event()


def _write_prog():
    try:
        if not progress_file:
            return
        speed = _state["speed"]
        done = _state["done"]
        total = _state["total"]
        eta = None
        if speed > 0 and total > 0 and done < total:
            eta = (total - done) / speed
        payload = {
            "downloaded": int(done), "total": int(total),
            "speed": round(speed, 1),
            "eta": eta,
            "downloaded_files": int(_state["ndone"]),
            "total_files": int(_state["nfiles"]),
            "current_file": _state.get("cur", ""),
            "detail": _state.get("detail", ""),
        }
        json.dump(payload, open(progress_file, "w"))
    except Exception:
        pass


def _monitor():
    pre = cache_size_bytes()
    lastt = time.time()
    while not _stop.is_set():
        _stop.wait(2.0)
        if _stop.is_set():
            break
        try:
            cur = cache_size_bytes()
            now = time.time()
            dt = now - lastt
            if dt >= 1:
                sp = (cur - pre) / dt if dt > 0 else 0
                if sp < 0:
                    sp = 0
                _state["speed"] = 0.5 * _state.get("speed", 0) + 0.5 * sp
                pre, lastt = cur, now
            _state["done"] = max(cur, _state.get("done", 0))
            if _state["total"] and _state["done"] > _state["total"]:
                _state["total"] = _state["done"]
            mb = lambda x: x / 1048576.0 if x else 0
            _state["detail"] = "正在下载 %s (%.1f / %s MB)" % (
                model_id, mb(_state["done"]),
                ("%.1f" % mb(_state["total"])) if _state["total"] else "?")
            _write_prog()
        except Exception:
            pass


def op_download():
    if progress_file:
        os.makedirs(Path(progress_file).parent, exist_ok=True)
    _stop.clear()
    threading.Thread(target=_monitor, daemon=True).start()
    try:
        if source == "modelscope":
            from modelscope import snapshot_download
            kw = dict(cache_dir=cache)
            if revision:
                kw["revision"] = revision
            if files:
                kw["allow_file_pattern"] = files
            p = snapshot_download(model_id, **kw)
            print("OK", p)
            sys.exit(0)
        else:
            from huggingface_hub import snapshot_download
            p = snapshot_download(repo_id=model_id, cache_dir=cache,
                                  revision=revision or None,
                                  allow_patterns=files or None,
                                  max_workers=2)
            print("OK", p)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        sys.stderr.write(tb[-1600:])
        print("ERR", str(e)[-800:])
        sys.exit(1)


if OP == "revisions":
    op_revisions()
elif OP == "download":
    op_download()
else:
    print("BAD_OP")
    sys.exit(2)