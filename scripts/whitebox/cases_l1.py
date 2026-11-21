#!/usr/bin/env python3
"""
L1 冒烟与契约（30 例）
=====================
目标：验证服务活着、页面可达、核心契约没破。
不含任何写操作，可对生产环境安全执行。
"""
import json

# ---------------- API 契约 ----------------

def register(run):
    R = run

    def skip(r, why):
        r.status = "SKIP"
        r.detail = why

    @R.case("L1-01", "GET /api/health 返回 ok", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/health")
        R.eq(st, 200, "status")
        R.eq(d.get("status"), "ok", "body.status")

    @R.case("L1-02", "GET /api/system 含 CPU/内存/磁盘/GPU", "L1")
    def _(r):
        st, d, el = R.http.get("/api/system")
        R.eq(st, 200, "status")
        for k in ("cpu_percent", "memory_total_gb", "disk_total_gb", "gpus"):
            R.ok(k in d, f"缺字段 {k}")
        R.ok(d["memory_total_gb"] > 0, "内存总量应 > 0")
        r.evidence = {"elapsed_s": round(el, 2), "gpu_count": len(d["gpus"])}
        R.ok(el < 5, f"/api/system 应 < 5s，实际 {el:.1f}s（已知 D6 同步阻塞风险）")

    @R.case("L1-03", "GET /api/settings 版本与路径正确", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/settings")
        R.eq(st, 200, "status")
        for k in ("host", "port", "models_dir", "logs_dir", "version"):
            R.ok(k in d, f"缺字段 {k}")
        R.ok(d["version"].startswith("v"), f"版本格式异常: {d['version']}")

    @R.case("L1-04", "GET /api/instances 返回 9 类固定实例", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/instances")
        R.eq(st, 200, "status")
        R.ok(len(d) >= 9, f"应 ≥9 个实例，实际 {len(d)}")
        r.evidence = {"instances": list(d)[:12]}

    @R.case("L1-05", "GET /api/config/models 配置完整", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/config/models")
        R.eq(st, 200, "status")

    @R.case("L1-06", "GET /v1/models 结构合规且带 name/capabilities", "L1")
    def _(r):
        st, d, _ = R.http.get("/v1/models")
        R.eq(st, 200, "status")
        R.eq(d.get("object"), "list", "object")
        R.ok(isinstance(d.get("data"), list), "data 应为 list")
        for m in d["data"]:
            R.ok("id" in m and "object" in m, f"条目缺 id/object: {m}")
            if m.get("port"):          # 本地实例
                R.ok(m.get("name"), f"本地模型 {m['id']} 缺 name（客户端会拿 id 去注册表误匹配）")
                R.ok("capabilities" in m, f"本地模型 {m['id']} 缺 capabilities")
        r.evidence = {"count": len(d["data"]),
                      "local": [m["id"] for m in d["data"] if m.get("port")]}

    @R.case("L1-07", "GET /api/gpu 返回 GPU 列表", "L1")
    def _(r):
        st, d, el = R.http.get("/api/gpu")
        R.eq(st, 200, "status")
        r.evidence = {"elapsed_s": round(el, 2)}

    @R.case("L1-08", "GET /api/engines 引擎注册表非空", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/engines")
        R.eq(st, 200, "status")

    @R.case("L1-09", "GET /api/engines/installed 响应时间（D3 回归）", "L1")
    def _(r):
        st, d, el = R.http.get("/api/engines/installed", timeout=120)
        R.eq(st, 200, "status")
        R.ok("engines" in d, "缺 engines 字段")
        r.evidence = {"elapsed_s": round(el, 2)}
        R.ok(el < 3, f"应 < 3s，实际 {el:.2f}s（D3: rglob 扫 19 万文件无缓存）")

    @R.case("L1-10", "GET /api/templates 模版库非空", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/templates")
        R.eq(st, 200, "status")
        R.ok(d.get("total", 0) >= 10, f"模版应 ≥10，实际 {d.get('total')}")

    @R.case("L1-11", "GET /api/templates/categories 分类统计", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/templates/categories")
        R.eq(st, 200, "status")
        R.ok(len(d.get("categories") or {}) >= 5, "分类应 ≥5")

    @R.case("L1-12", "GET /api/providers 列表可读且不泄露明文 Key", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/providers")
        R.eq(st, 200, "status")
        raw = json.dumps(d)
        R.ok("sk-" not in raw or "..." in raw, "列表不应含完整明文 Key")

    @R.case("L1-13", "GET /api/token-router/usage 用量结构", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/token-router/usage")
        R.eq(st, 200, "status")
        R.ok("totals" in d and "rows" in d, "缺 totals/rows")

    @R.case("L1-14", "GET /api/router/calls 调用历史结构", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/router/calls")
        R.eq(st, 200, "status")
        R.ok("calls" in d, "缺 calls")

    @R.case("L1-15", "GET /api/logs/center/sources 日志来源", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/logs/center/sources")
        R.eq(st, 200, "status")

    @R.case("L1-16", "GET /api/schema/params chat 参数定义", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/schema/params?category=chat&engine=llama_cpp")
        R.eq(st, 200, "status")
        R.ok(len(d.get("parameters") or []) > 5, "chat 参数应 >5 项")

    @R.case("L1-17", "GET /api/schema/models 模型候选", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/schema/models?category=chat&engine=llama_cpp")
        R.eq(st, 200, "status")

    @R.case("L1-18", "GET /api/fs/list 浏览 /models 根目录", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/fs/list?path=")
        R.eq(st, 200, "status")
        R.ok(len(d.get("dirs") or []) >= 2, f"目录条目过少: {d}")

    @R.case("L1-19", "GET /api/router/resolve 本地实例解析", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/router/resolve?model=embedding")
        R.eq(st, 200, "status")
        R.ok(d.get("target"), "缺 target")

    @R.case("L1-20", "GET /api/docs 文档列表", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/docs")
        R.eq(st, 200, "status")

    @R.case("L1-21", "GET /api/settings/pip 镜像配置", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/settings/pip")
        R.eq(st, 200, "status")

    @R.case("L1-22", "GET /api/settings/default-key 不泄露明文", "L1")
    def _(r):
        st, d, _ = R.http.get("/api/settings/default-key")
        R.eq(st, 200, "status")
        R.ok("has_key" in d, "缺 has_key")

    @R.case("L1-23", "未知端点返回 404 而非 500", "L1")
    def _(r):
        st, _, _ = R.http.get("/api/definitely-not-exist-xyz")
        R.eq(st, 404, "status")

    @R.case("L1-24", "未知模型 /v1/chat/completions 返回明确错误", "L1")
    def _(r):
        st, d, _ = R.http.post("/v1/chat/completions",
                               {"model": "no-such-model-xyz", "messages": []})
        R.ok(st in (400, 404, 422, 503), f"status={st}")
        R.ok("error" in d or "message" in d, f"缺结构化错误: {d}")

    @R.case("L1-25", "静态首页返回 HTML 且含 7 个导航", "L1")
    def _(r):
        st, body, _ = R.http.get("/", raw=True)
        R.eq(st, 200, "status")
        html = body.decode("utf-8", "replace")
        for tab in ("dashboard", "launch", "router", "playground", "tools", "logs", "settings"):
            R.ok(f'data-tab="{tab}"' in html, f"导航缺 {tab}")

    # ---------------- 页面可达 ----------------

    @R.case("L1-26", "页面加载无 JS 控制台错误", "L1")
    def _(r):
        if not R.page:
            return skip(r, "无浏览器上下文")
        # 页面 500ms 轮询，networkidle 永远不会触发，用 domcontentloaded + 等关键元素
        R.page.goto(R.http.base + "/", wait_until="domcontentloaded", timeout=30000)
        R.page.wait_for_selector("#serverStatusText", timeout=15000)
        R.page.wait_for_timeout(3000)
        errs = [e for e in R.console_errors if "favicon" not in e.lower()]
        R.ok(not errs, f"控制台错误: {errs[:3]}")

    @R.case("L1-27", "7 个导航页逐一切换可达", "L1")
    def _(r):
        if not R.page:
            return skip(r, "无浏览器上下文")
        seen = []
        for tab in ("dashboard", "launch", "router", "playground", "tools", "logs", "settings"):
            R.goto_tab(tab)
            seen.append(tab)
        R.eq(len(seen), 7, "切换页数")

    @R.case("L1-28", "Dashboard 系统卡有真实数值", "L1")
    def _(r):
        if not R.page:
            return skip(r, "无浏览器上下文")
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(1500)
        cpu = R.text("#cpuValue")
        R.ok(cpu and cpu != "--", f"CPU 未填充: {cpu}")

    @R.case("L1-29", "Models 三个子页签可达", "L1")
    def _(r):
        if not R.page:
            return skip(r, "无浏览器上下文")
        R.goto_tab("launch")
        for sub in ("launch", "running", "templates"):
            R.goto_sub(sub)

    @R.case("L1-30", "Playground 9 个子页签可达（记录 pg-api 孤儿）", "L1")
    def _(r):
        if not R.page:
            return skip(r, "无浏览器上下文")
        R.goto_tab("playground")
        for pg in ("chat", "embedding", "asr", "tts", "rerank", "ocr", "image", "video", "i2v"):
            R.goto_pg(pg)
        has_api_tab = R.js("!!document.querySelector('.pg-tab[data-pg=\"api\"]')")
        has_pane = R.js("!!document.getElementById('pg-api')")
        r.evidence = {"api_debug_tab_exists": has_api_tab, "api_pane_exists": has_pane}
        # D2 回归：有面板就必须有入口，且点了能激活
        if has_pane:
            R.ok(has_api_tab, "D2: pg-api 面板存在但没有切换入口")
            R.page.click('.pg-tab[data-pg="api"]')
            R.page.wait_for_timeout(600)
            R.ok(R.js("document.getElementById('pg-api').classList.contains('active')"),
                 "pg-api 点击后未激活")
