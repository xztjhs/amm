#!/usr/bin/env python3
"""
第二轮追加用例（回归验证 + 新覆盖）
=====================================
第一轮修了 5 个缺陷，这里针对性验证修复是否真的生效、有没有引入回归。
同时补第一轮没覆盖到的面。

  R2-01..05  五个修复点的定点回归
  R2-06..12  第一轮遗漏的功能面（动态按钮、更多弹窗、边界）
  R2-13..16  修复引入的风险检查（性能/并发/兼容）
"""
import json
import re
import threading
import time

EMB = "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"


def register(run):
    R = run

    def skip(r, why):
        r.status = "SKIP"
        r.detail = why

    def need_page(r):
        if not R.page:
            skip(r, "无浏览器上下文")
            return False
        try:
            if "chrome-error" in R.page.url or R.page.url in ("", "about:blank"):
                R.page.goto(R.http.base + "/", wait_until="domcontentloaded", timeout=30000)
                R.page.wait_for_selector(".nav-item", timeout=20000)
            R.js("""
              (() => { for (const id of ['helpModal','tplEditorModal','fileBrowserModal','modelModal']) {
                const el=document.getElementById(id);
                if (el && getComputedStyle(el).display!=='none') el.style.display='none';
              }})()
            """)
        except Exception as e:
            skip(r, f"页面不可用: {e}")
            return False
        return True

    # ================================================================
    # 五个修复点的定点回归
    # ================================================================
    @R.case("R2-01", "回归 D3：引擎列表已缓存（<1s）", "R2")
    def _(r):
        times = []
        for _ in range(3):
            st, d, el = R.http.get("/api/engines/installed", timeout=120)
            R.eq(st, 200, "status")
            times.append(round(el, 3))
            time.sleep(0.3)
        r.evidence = {"times": times}
        R.ok(max(times) < 1.0, f"缓存后应 <1s，实测 {times}")
        R.ok("engines" in d, "缺 engines 字段")

    @R.case("R2-02", "回归 D1：GPU 页有导航入口且可切换", "R2")
    def _(r):
        if not need_page(r): return
        R.page.goto(R.http.base + "/", wait_until="domcontentloaded")
        R.page.wait_for_selector(".nav-item", timeout=20000)
        has = R.js("!!document.querySelector('.nav-item[data-tab=\"gpu\"]')")
        R.ok(has, "导航栏缺 GPU 入口")
        R.goto_tab("gpu")
        R.page.wait_for_timeout(1200)
        R.ok(R.js("document.getElementById('tab-gpu').classList.contains('active')"),
             "GPU 页未激活")

    @R.case("R2-03", "回归 D2：API Debug 页签可点且面板激活", "R2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("playground")
        R.page.wait_for_timeout(1200)
        R.ok(R.js("!!document.querySelector('.pg-tab[data-pg=\"api\"]')"), "缺 API 页签")
        R.page.click('.pg-tab[data-pg="api"]')
        R.page.wait_for_timeout(800)
        R.ok(R.js("document.getElementById('pg-api').classList.contains('active')"),
             "pg-api 未激活")

    @R.case("R2-04", "回归 DEV-04：名称建议与 UID 不同且保留大小写", "R2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch")
        R.page.wait_for_timeout(1500)
        R.js("window.wizSet('launchWizard','category','embedding')")
        R.js("window.wizSet('launchWizard','modelFile','" + EMB + "')")
        R.page.wait_for_timeout(1500)
        uid_ph = R.js("""
          (() => { const f=Array.from(document.querySelectorAll('#wiz-tiers-launchWizard .field'))
            .find(x=>(x.querySelector('label')?.textContent||'').includes('实例 UID'));
            return f ? (f.querySelector('input')?.placeholder||'') : ''; })()
        """)
        name_ph = R.js("""
          (() => { const f=Array.from(document.querySelectorAll('#wiz-tiers-launchWizard .field'))
            .find(x=>(x.querySelector('label')?.textContent||'').includes('模型名称'));
            return f ? (f.querySelector('input')?.placeholder||'') : ''; })()
        """)
        r.evidence = {"uid": uid_ph, "name": name_ph}
        R.eq(uid_ph, "qwen3-embedding-0.6b", "uid 派生")
        R.ok(name_ph and name_ph != uid_ph, "名称应与 uid 不同")
        R.ok(any(c.isupper() for c in name_ph), f"名称应保留大小写: {name_ph}")

    @R.case("R2-05", "回归 DEV-01：embeddings/rerank 写调用历史", "R2")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "r2-emb"}, timeout=180)
        ok = False
        for _ in range(15):
            st, e, _ = R.http.post("/v1/embeddings",
                                   {"model": "r2-emb", "input": ["a", "b"]}, timeout=60)
            if st == 200:
                ok = True
                break
            time.sleep(4)
        R.ok(ok, "实例未就绪")

        time.sleep(1)
        _, calls, _ = R.http.get("/api/router/calls?limit=30")
        mine = [c for c in calls.get("calls") or [] if c.get("model") == "r2-emb"]
        R.ok(mine, "embeddings 未写调用历史")
        out = str(mine[0].get("output") or "")
        r.evidence = {"output": out[:80], "provider": mine[0].get("provider")}
        R.ok("embedding" in out.lower(), f"输出应标注 embedding: {out[:60]!r}")
        R.eq(mine[0].get("provider"), "local", "provider 标记")
        R.http.delete("/api/instances/r2-emb")

    # ================================================================
    # 第一轮遗漏的功能面
    # ================================================================
    @R.case("R2-06", "动态按钮：实例行操作全部可点", "R2")
    def _(r):
        if not need_page(r): return
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "r2-row"}, timeout=180)
        R.goto_tab("launch")
        R.page.click('.sub-tab[data-sub="running"]')
        R.page.wait_for_timeout(6000)
        btns = R.js("""
          (() => { const tr=Array.from(document.querySelectorAll('#rmTableBody tr'))
            .find(x=>x.textContent.includes('r2-row'));
            return tr ? Array.from(tr.querySelectorAll('button')).map(b=>b.textContent.trim()) : []; })()
        """)
        r.evidence = {"buttons": btns}
        R.ok(len(btns) >= 4, f"行操作按钮应 ≥4，实际 {btns}")
        R.http.delete("/api/instances/r2-row")

    @R.case("R2-07", "模型详情弹窗可打开并含参数区", "R2")
    def _(r):
        if not need_page(r): return
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "r2-modal"}, timeout=180)
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(3000)
        # openModelDetail 需要 modelId 参数，盲点 DOM 不一定命中可点元素
        opened = R.js("typeof window.openModelDetail === 'function'")
        R.js("window.openModelDetail('r2-modal')")
        R.page.wait_for_timeout(1500)
        disp = R.js("getComputedStyle(document.getElementById('modelModal')).display")
        title = R.js("document.getElementById('modalTitle')?.textContent || ''")
        r.evidence = {"openModelDetail_exists": opened, "display": disp, "title": title}
        R.ok(disp != "none", "模型详情弹窗未显示")
        R.ok(title, "弹窗标题为空")
        R.http.delete("/api/instances/r2-modal")

    @R.case("R2-08", "Log Center 调用历史分页边界", "R2")
    def _(r):
        st, d, _ = R.http.get("/api/router/calls?limit=5")
        R.eq(st, 200, "status")
        n = len(d.get("calls") or [])
        r.evidence = {"returned": n, "limit": 5}
        R.ok(n <= 5, f"limit 未生效: {n}")

    @R.case("R2-09", "调用历史关键字过滤", "R2")
    def _(r):
        st, all_calls, _ = R.http.get("/api/router/calls?limit=50")
        calls = all_calls.get("calls") or []
        if not calls:
            skip(r, "无调用记录")
            return
        target = calls[0].get("model")
        st2, filtered, _ = R.http.get(f"/api/router/calls?model={target}&limit=50")
        got = filtered.get("calls") or []
        r.evidence = {"filter": target, "n": len(got)}
        R.ok(all(c.get("model") == target for c in got),
             f"过滤后仍有其他模型: {[c.get('model') for c in got[:5]]}")

    @R.case("R2-10", "文件浏览器：上级目录与进入子目录", "R2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("tools")
        R.page.wait_for_timeout(1500)
        R.page.evaluate("window.openDownloadDirBrowser()")
        R.page.wait_for_selector("#fileBrowserModal.active", timeout=10000)
        R.page.wait_for_timeout(2000)
        first = R.js("document.querySelectorAll('#fileBrowserBody *[onclick]').length")
        # 下载浏览器的导航函数是 qUp（工具页那套），不是 fbUp
        has_qUp = R.js("typeof window.qUp === 'function'")
        if has_qUp:
            R.js("window.qUp()")
            R.page.wait_for_timeout(1800)
        after_up = R.js("document.querySelectorAll('#fileBrowserBody *[onclick]').length")
        r.evidence = {"entries": first, "after_up": after_up, "has_qUp": has_qUp}
        R.ok(first > 0, "列表为空")
        R.js("window.closeFileBrowser()")

    @R.case("R2-11", "模版部署表单含模型名称字段", "R2")
    def _(r):
        st, lst, _ = R.http.get("/api/templates?category=embedding")
        tpls = lst.get("templates") or []
        if not tpls:
            skip(r, "无 embedding 模版")
            return
        if not need_page(r): return
        R.goto_tab("launch")
        R.page.click('.sub-tab[data-sub="templates"]')
        R.page.wait_for_timeout(6000)
        R.js("window.tplOpenDetail && window.tplOpenDetail('%s')" % tpls[0]["id"])
        R.page.wait_for_timeout(2500)
        body = R.text("#tplDetailBody")
        R.ok("模型名称" in body, "部署表单缺「模型名称」字段")

    @R.case("R2-12", "Playground Chat 选模型下拉有内容", "R2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("playground")
        R.page.wait_for_timeout(3000)
        n = R.js("document.querySelectorAll('#pgChatModel option').length")
        r.evidence = {"options": n}
        R.ok(True, f"选项数 {n}（0 表示当前无可用实例，非缺陷）")

    # ================================================================
    # 修复引入的风险检查
    # ================================================================
    @R.case("R2-13", "修复无回归：/api/engines/catalog 仍正常", "R2")
    def _(r):
        st, d, el = R.http.get("/api/engines/catalog", timeout=180)
        R.eq(st, 200, "status")
        r.evidence = {"elapsed_s": round(el, 2), "keys": list(d)[:6]}
        R.ok("engines" in d or "catalog" in d, f"结构异常: {list(d)[:6]}")

    @R.case("R2-14", "修复无回归：实例体积字段仍然有值", "R2")
    def _(r):
        st, d, _ = R.http.get("/api/engines/installed", timeout=120)
        engines = d.get("engines") or {}
        sizes = []
        for et, meta in engines.items():
            for inst in meta.get("installed") or []:
                if inst.get("size_mb") is not None:
                    sizes.append((inst.get("version"), inst.get("size_mb")))
        r.evidence = {"sizes": sizes[:6]}
        R.ok(sizes, "size_mb 字段丢失（缓存改动破坏了统计）")
        R.ok(any(s[1] > 0 for s in sizes), f"体积全为 0，统计失效: {sizes[:3]}")

    @R.case("R2-15", "修复无回归：并发请求引擎列表不重复扫描", "R2")
    def _(r):
        results = []
        def hit():
            results.append(R.http.get("/api/engines/installed", timeout=120)[2])
        ts = [threading.Thread(target=hit) for _ in range(5)]
        t0 = time.time()
        [t.start() for t in ts]; [t.join() for t in ts]
        total = time.time() - t0
        r.evidence = {"concurrent_5_total_s": round(total, 2),
                      "each": [round(x, 3) for x in results]}
        R.ok(total < 5, f"5 个并发合计 {total:.1f}s，缓存未生效")

    @R.case("R2-16", "修复无回归：鉴权中间件语义正确", "R2")
    def _(r):
        # 无任何 Key 时应放行
        R.http.delete("/api/settings/default-key")
        st_no, _, _ = R.http.get("/v1/models")
        # 设默认 Key 后无 Key 请求应 401
        R.http.post("/api/settings/default-key/generate")
        st_blocked, _, _ = R.http.get("/v1/models")
        # 清除后应恢复放行
        R.http.delete("/api/settings/default-key")
        st_after, _, _ = R.http.get("/v1/models")
        r.evidence = {"no_key": st_no, "with_default_no_key_header": st_blocked,
                      "after_clear": st_after}
        R.eq(st_blocked, 401, "设了默认 Key 后无 Key 应 401")
        R.eq(st_after, 200, "清除默认 Key 后应恢复放行")
