#!/usr/bin/env python3
"""
白盒 E2E 执行入口
=================
  --base          被测地址
  --layer         L1|L2|L3|L4|ALL
  --no-browser    纯 API 模式（L1 的 API 部分）
  --destructive   允许执行破坏性用例
  --headful       有头模式（调试用）
"""
import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner import Runner, OUT_ROOT, DESTRUCTIVE_IDS


def collect(module) -> list:
    """从模块里收集所有被 @R.case 装饰的函数，按注册顺序"""
    out = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_case"):
            out.append(obj)
    # dir() 是字母序，用例 ID 本身有序，按 ID 排序
    out.sort(key=lambda f: f._case[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://<YOUR_HOST>:60009")
    ap.add_argument("--layer", default="L1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--destructive", action="store_true")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="", help="只跑这些 ID 前缀，逗号分隔，如 G,D,W")
    args = ap.parse_args()

    layers = ["L1", "L2", "L3", "L4"] if args.layer.upper() == "ALL" else [args.layer.upper()]

    page = None
    browser = None
    pw = None
    if not args.no_browser:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=not args.headful,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()

    run = Runner(args.base, page=page, destructive_ok=args.destructive)
    if page:
        page.on("console", lambda m: (
            run.console_errors.append(f"{m.type}: {m.text}")
            if m.type == "error" else None))
        page.on("requestfailed",
                lambda r: run.failed_requests.append(f"{r.method} {r.url} {r.failure}"))

    total = 0
    try:
        for layer in layers:
            mod_name = f"cases_{layer.lower()}"
            try:
                mod = __import__(mod_name)
            except ImportError as e:
                print(f"[skip] {mod_name} 不存在: {e}")
                continue
            print(f"\n=== {layer} ===")
            mod.register(run)
            sel = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else []
            for fn in run.pending():
                if fn._case[2] != layer:
                    continue
                if sel and not any(fn._case[0].startswith(x) for x in sel):
                    continue
                cid = fn._case[0]
                run.run_case(fn, destructive=cid in DESTRUCTIVE_IDS)
                total += 1
                # 破坏性/鉴权类用例跑完立刻恢复鉴权状态，避免污染后续
                if cid in DESTRUCTIVE_IDS or cid in ("L3-08",):
                    try:
                        run.restore_auth()
                    except Exception:
                        pass
    finally:
        name = args.out or f"result-{args.layer.lower()}-{int(__import__('time').time())}.json"
        run.save(name)
        if browser:
            browser.close()
        if pw:
            pw.stop()
    print(f"\n执行 {total} 例")


if __name__ == "__main__":
    main()
