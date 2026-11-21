#!/usr/bin/env python3
"""
AMM 白盒 E2E 测试驱动
=====================
用真实浏览器（Chromium CDP）驱动 AMM WebUI，配合 HTTP 断言做交叉验证。

设计原则：
  - UI 断言 + API 断言双证据：点了按钮既要看页面变了，也要看接口真的被调用
  - 每个用例独立失败：一条挂掉不影响后续，异常记录为 FAIL 并留存上下文
  - 破坏性用例显式标记 destructive=True，便于分批执行和事后审计
  - 所有产出落到 docs/whitebox-r1/ ：JSON 结果 + 截图 + 失败详情

用法：
  python3 scripts/whitebox/runner.py --base http://<YOUR_HOST>:60009 --layer L1
  python3 scripts/whitebox/runner.py --base ... --layer L2 --only G,D,W
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import urllib.request
import urllib.error

OUT_ROOT = Path(__file__).resolve().parents[2] / "docs" / "whitebox-r1"

# 业务依赖顺序：先建（W）后管（R）；破坏性用例永远最后
ORDER_GROUPS = {"G": 1, "D": 2, "W": 3, "R": 4, "T": 5, "P": 6,
                "K": 7, "L": 8, "S": 9, "X": 10, "R2": 11, "N": 12,
                "L1": 0, "L3": 0, "L4": 0}

DESTRUCTIVE_IDS = {
    "L2-45",  # 结束运行实例
    "L2-46",  # 删除模版
    "L2-47",  # 吊销 Key
    "L2-48",  # 删除 provider
    "L2-49",  # 日志清理
    "L2-50",  # 重启服务
    "L2-51",  # 引擎卸载
    "L2-52",  # 清除默认 Key
    "L3-05",  # 故障恢复
}


# ----------------------------------------------------------------------
# HTTP 工具（不依赖第三方库，避免在测试机上装依赖）
# ----------------------------------------------------------------------
class Http:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def request(self, method: str, path: str, body=None, headers=None,
                timeout: float = 60.0, raw: bool = False):
        url = self.base + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                elapsed = time.time() - t0
                if raw:
                    return r.status, payload, elapsed
                try:
                    return r.status, json.loads(payload.decode("utf-8", "replace")), elapsed
                except Exception:
                    return r.status, payload.decode("utf-8", "replace"), elapsed
        except urllib.error.HTTPError as e:
            payload = e.read()
            elapsed = time.time() - t0
            try:
                return e.code, json.loads(payload.decode("utf-8", "replace")), elapsed
            except Exception:
                return e.code, payload.decode("utf-8", "replace"), elapsed
        except Exception as e:
            return 0, {"error": str(e)}, time.time() - t0

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


# ----------------------------------------------------------------------
# 用例模型
# ----------------------------------------------------------------------
@dataclass
class Result:
    id: str
    name: str
    layer: str
    status: str = "PENDING"       # PENDING | PASS | FAIL | SKIP | BLOCKED
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    screenshot: str = ""
    destructive: bool = False
    duration: float = 0.0


class Runner:
    def __init__(self, base: str, page=None, out_dir: Path = OUT_ROOT,
                 destructive_ok: bool = False):
        self.http = Http(base)
        self.page = page            # Playwright Page（可空，纯 API 模式）
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.shot_dir = out_dir / "shots"
        self.shot_dir.mkdir(exist_ok=True)
        self.results: List[Result] = []
        self.destructive_ok = destructive_ok
        self.console_errors: List[str] = []
        self.failed_requests: List[str] = []
        self._registered: List[Callable] = []

    # ---------------- 执行 ----------------
    def case(self, cid: str, name: str, layer: str):
        def deco(fn: Callable[[Result], None]):
            fn._case = (cid, name, layer)
            self._registered.append(fn)
            return fn
        return deco

    def pending(self) -> List[Callable]:
        """已注册但还没跑过的用例，按显式业务顺序排序。

        不能按字母序：R（管理实例）依赖 W（创建实例）先跑，
        破坏性用例（删实例/重启服务）必须最后，否则会把后续用例全带塌。
        """
        done = {r.id for r in self.results}
        todo = [f for f in self._registered if f._case[0] not in done]

        def sort_key(f):
            cid = f._case[0]
            if cid in DESTRUCTIVE_IDS:
                return (99, cid)
            # 分组前缀 -> 业务顺序
            prefix = cid.split("-")[0]
            order = ORDER_GROUPS.get(prefix, 50)
            # 同组内按数字序号，而不是字符串（L2-45 不能插到 L-09 前面）
            tail = cid.split("-", 1)[-1]
            try:
                num = int(tail)
            except ValueError:
                num = 0
            return (order, num, cid)

        return sorted(todo, key=sort_key)

    def run_case(self, fn, destructive: bool = False):
        cid, name, layer = fn._case
        r = Result(id=cid, name=name, layer=layer, destructive=destructive)
        if destructive and not self.destructive_ok:
            r.status = "SKIP"
            r.detail = "破坏性用例未授权（--destructive）"
            self.results.append(r)
            return r
        t0 = time.time()
        try:
            fn(r)
            if r.status == "PENDING":
                r.status = "PASS"       # 用例没主动改状态 = 断言全过
        except AssertionError as e:
            r.status = "FAIL"
            r.detail = f"断言失败: {e}\n{traceback.format_exc(limit=3)}"
        except Exception as e:
            r.status = "FAIL"
            r.detail = f"异常: {e}\n{traceback.format_exc(limit=3)}"
        r.duration = round(time.time() - t0, 2)
        if r.status == "FAIL":
            self.shot(r)
        self.results.append(r)
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭", "BLOCKED": "⛔"}.get(r.status, "?")
        print(f"  {icon} {cid} {name}" + (f" — {r.detail.splitlines()[0][:110]}" if r.status == "FAIL" else ""))
        return r

    # ---------------- 断言辅助 ----------------
    def shot(self, r: Result, label: str = "") -> str:
        if not self.page:
            return ""
        try:
            p = self.shot_dir / f"{r.id}{('-' + label) if label else ''}.png"
            self.page.screenshot(path=str(p), full_page=False)
            r.screenshot = str(p.relative_to(self.out_dir))
            return r.screenshot
        except Exception:
            return ""

    def ok(self, cond, msg):
        if not cond:
            raise AssertionError(msg)

    def eq(self, got, want, msg=""):
        if got != want:
            raise AssertionError(f"{msg} 期望={want!r} 实际={got!r}")

    def contains(self, hay, needle, msg=""):
        if needle not in hay:
            raise AssertionError(f"{msg} 「{needle}」不在结果里: {str(hay)[:300]}")

    # ---------------- 浏览器辅助 ----------------
    def goto_tab(self, tab: str):
        """点击顶部导航切页，等 section active"""
        self.page.click(f'.nav-item[data-tab="{tab}"]')
        self.page.wait_for_selector(f"#tab-{tab}.active", timeout=8000)

    def goto_sub(self, sub: str):
        self.page.click(f'.sub-tab[data-sub="{sub}"]')
        self.page.wait_for_selector(f"#ms-{sub}.active", timeout=8000)

    def goto_pg(self, pg: str):
        self.page.click(f'.pg-tab[data-pg="{pg}"]')
        self.page.wait_for_selector(f"#pg-{pg}.active", timeout=8000)

    def text(self, sel: str, timeout: int = 8000) -> str:
        return (self.page.inner_text(sel, timeout=timeout) or "").strip()

    def wait_api(self, path_fragment: str, action: Callable, timeout: int = 20000):
        """执行 action 并等待某个 API 响应（网络层证据）"""
        with self.page.expect_response(
                lambda r: path_fragment in r.url, timeout=timeout) as info:
            action()
        return info.value

    def js(self, expr: str):
        return self.page.evaluate(expr)

    # ---------------- 环境守卫 ----------------
    def snapshot_env(self) -> dict:
        """记录关键环境变量，破坏性用例跑完后用于自检/恢复。"""
        _, provs, _ = self.http.get("/api/providers")
        _, keys, _ = self.http.get("/api/token-router/keys")
        snap = {
            "providers": {p["name"]: {"type": p.get("type"),
                                      "base_url": p.get("base_url"),
                                      "has_key": p.get("has_key")}
                          for p in (provs.get("providers") or [])},
            "key_count": len(keys.get("keys") or []),
        }
        return snap

    def restore_auth(self) -> None:
        """把鉴权状态恢复到「无 Key、免鉴权」，避免用例互相污染。"""
        self.http.delete("/api/settings/default-key")
        _, kl, _ = self.http.get("/api/token-router/keys")
        for k in kl.get("keys") or []:
            kid = k.get("key") or k.get("key_id")
            if kid:
                self.http.delete(f"/api/token-router/keys/{kid}")

    # ---------------- 汇总 ----------------
    def summary(self) -> Dict[str, Any]:
        by_layer: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            d = by_layer.setdefault(r.layer, {"PASS": 0, "FAIL": 0, "SKIP": 0, "BLOCKED": 0, "total": 0})
            d[r.status] = d.get(r.status, 0) + 1
            d["total"] += 1
        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base": self.http.base,
            "total": len(self.results),
            "pass": sum(1 for r in self.results if r.status == "PASS"),
            "fail": sum(1 for r in self.results if r.status == "FAIL"),
            "skip": sum(1 for r in self.results if r.status == "SKIP"),
            "blocked": sum(1 for r in self.results if r.status == "BLOCKED"),
            "by_layer": by_layer,
            "console_errors": self.console_errors[:50],
            "failed_requests": self.failed_requests[:50],
            "results": [asdict(r) for r in self.results],
        }

    def save(self, name: str) -> Path:
        p = self.out_dir / name
        p.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=1), encoding="utf-8")
        s = self.summary()
        print(f"\n{'='*60}")
        print(f"总计 {s['total']}  通过 {s['pass']}  失败 {s['fail']}  "
              f"跳过 {s['skip']}  阻塞 {s['blocked']}")
        for layer, d in s["by_layer"].items():
            print(f"  {layer}: PASS {d['PASS']} / FAIL {d['FAIL']} / SKIP {d['SKIP']} / total {d['total']}")
        print(f"结果: {p}")
        return p
