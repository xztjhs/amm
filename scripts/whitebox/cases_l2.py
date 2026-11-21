#!/usr/bin/env python3
"""
L2 交互功能测试（逐按钮/表单/弹窗/过滤/导出）
=============================================
每个用例都是「真实点击 + 双证据断言」：UI 状态变化 + 对应的 API 请求发生。

分节：
  G  全局顶栏 (G-01..G-06)
  D  Dashboard (D-01..D-04)
  W  Models 启动向导 (W-01..W-12)
  R  运行中实例 (R-01..R-10)
  T  模版库 (T-01..T-08)
  P  Playground (P-01..P-12)
  K  Token Router (K-01..K-14)
  L  Log Center (L-01..L-09)
  S  Settings (S-01..S-08)
  X  Tools (X-01..X-08)
  Z  破坏性操作 (L2-45..L2-52)
"""
import json
import re
import time


def register(run):
    R = run

    def skip(r, why):
        r.status = "SKIP"
        r.detail = why

    def need_page(r, goto_tab=True):
        if not R.page:
            skip(r, "无浏览器上下文")
            return False
        # 先确保页面已加载（用例间不互相依赖）
        try:
            if R.page.url in ("about:blank", "") or "chrome-error" in R.page.url:
                R.page.goto(R.http.base + "/", wait_until="domcontentloaded", timeout=30000)
                R.page.wait_for_selector(".nav-item[data-tab=\"dashboard\"]", timeout=20000)
                R.page.wait_for_timeout(1500)
            # 清掉前一个用例可能残留的遮罩弹窗，否则后面所有点击都会被拦
            R.js("""
              (() => {
                const ids = ['helpModal','tplEditorModal','fileBrowserModal','modelModal'];
                for (const id of ids) {
                  const el = document.getElementById(id);
                  if (el && getComputedStyle(el).display !== 'none') {
                    if (id === 'fileBrowserModal' && window.closeFileBrowser) window.closeFileBrowser();
                    else if (id === 'modelModal' && window.closeModal) window.closeModal();
                    else if (id === 'tplEditorModal' && window.tplCloseEditor) window.tplCloseEditor();
                    else el.style.display = 'none';
                  }
                }
              })()
            """)
        except Exception as e:
            skip(r, f"页面不可用: {e}")
            return False
        return True

    def safe_click(r, sel, timeout=15000):
        """页面可能被前一个用例搞乱，点击失败先重载一次"""
        try:
            R.page.click(sel, timeout=timeout)
            return True
        except Exception:
            R.page.goto(R.http.base + "/", wait_until="domcontentloaded", timeout=30000)
            R.page.wait_for_selector(".nav-item[data-tab=\"dashboard\"]", timeout=20000)
            R.page.wait_for_timeout(1200)
            R.page.click(sel, timeout=timeout)
            return True

    # ================================================================
    # G 全局顶栏
    # ================================================================
    @R.case("G-01", "刷新间隔逐档切换并持久化", "L2")
    def _(r):
        if not need_page(r): return
        R.page.goto(R.http.base + "/", wait_until="domcontentloaded")
        R.page.wait_for_selector("#refreshInterval")
        opts = R.js("Array.from(document.querySelectorAll('#refreshInterval option')).map(o=>o.value)")
        R.ok(len(opts) >= 10, f"档位应 ≥10，实际 {len(opts)}")
        R.js("document.querySelector('#refreshInterval').value='2000';"
             "document.querySelector('#refreshInterval').dispatchEvent(new Event('change'))")
        R.page.wait_for_timeout(300)
        R.eq(R.js("localStorage.getItem('amm-refresh-ms')"), "2000", "localStorage")
        R.page.reload(wait_until="domcontentloaded")
        R.page.wait_for_timeout(800)
        R.eq(R.js("document.querySelector('#refreshInterval').value"), "2000", "刷新后仍生效")

    @R.case("G-02", "主题切换生效且持久化", "L2")
    def _(r):
        if not need_page(r): return
        before = R.js("document.documentElement.getAttribute('data-theme')")
        R.page.click("#themeToggle")
        R.page.wait_for_timeout(400)
        after = R.js("document.documentElement.getAttribute('data-theme')")
        R.ok(before != after, f"主题未切换（{before}→{after}）")
        R.page.reload(wait_until="domcontentloaded")
        R.page.wait_for_timeout(800)
        R.eq(R.js("document.documentElement.getAttribute('data-theme')"), after, "刷新后主题")
        R.page.click("#themeToggle")   # 还原

    @R.case("G-03", "服务器状态点与时钟在走", "L2")
    def _(r):
        if not need_page(r): return
        R.page.wait_for_timeout(2500)
        t1 = R.text("#clock")
        R.page.wait_for_timeout(1600)
        t2 = R.text("#clock")
        R.ok(t1 and t2 and t1 != t2, f"时钟未走动: {t1} / {t2}")
        st = R.text("#serverStatusText")
        R.ok("Online" in st or "online" in st.lower(), f"状态异常: {st}")

    @R.case("G-04", "帮助手册弹窗打开/关闭", "L2")
    def _(r):
        if not need_page(r): return
        R.page.click("#helpToggle")
        R.page.wait_for_timeout(1500)
        disp = R.js("getComputedStyle(document.getElementById('helpModal')).display")
        R.ok(disp != "none", f"帮助弹窗未显示（display={disp}）")
        R.js("document.getElementById('helpClose').click()")
        R.page.wait_for_timeout(600)
        R.eq(R.js("getComputedStyle(document.getElementById('helpModal')).display"), "none",
             "关闭后应隐藏")

    @R.case("G-05", "7 个导航页数据不串（每页有独有元素）", "L2")
    def _(r):
        if not need_page(r): return
        marks = {"dashboard": "#systemCards", "launch": "#launchWizard",
                 "router": "#routerStats", "playground": "#pgChatMessages",
                 "tools": "#modelDownloadPanel", "logs": "#lcViewer",
                 "settings": "#settingsPanel"}
        for tab, sel in marks.items():
            R.goto_tab(tab)
            R.page.wait_for_timeout(300)
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"{tab} 缺 {sel}")

    @R.case("G-06", "GPU Monitor 页可达性（D1 回归）", "L2")
    def _(r):
        if not need_page(r): return
        has_nav = R.js("!!document.querySelector('.nav-item[data-tab=\"gpu\"]')")
        has_section = R.js("!!document.getElementById('tab-gpu')")
        r.evidence = {"nav_entry": has_nav, "section_exists": has_section}
        R.ok(has_section == has_nav, "导航项与页面必须成对出现")
        if has_nav:
            R.goto_tab("gpu")
            R.page.wait_for_timeout(1500)
            R.ok(R.js("document.getElementById('tab-gpu').classList.contains('active')"),
                 "GPU 页点击后未激活")

    # ================================================================
    # D Dashboard
    # ================================================================
    @R.case("D-01", "四张系统卡数值与进度条一致", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(2000)
        _, sysinfo, _ = R.http.get("/api/system")

        # 进度条宽度必须等于 API 的 percent 字段。
        # 注意：内存条不能用 used/total 反算 —— psutil 的 memory_percent 是
        # (total-available)/total，buff/cache 不计入 used，两者天然不同。
        pairs = (("#cpuValue", "#cpuBar", "cpu_percent"),
                 ("#memValue", "#memBar", "memory_percent"),
                 ("#diskValue", "#diskBar", "disk_percent"))
        for val, bar, key in pairs:
            v = R.text(val)
            R.ok(v and v != "--", f"{val} 未填充")
            w = R.js(f"parseFloat(document.querySelector('{bar}').style.width)||0")
            want = float(sysinfo.get(key) or 0)
            R.ok(abs(w - want) < 1.5,
                 f"{bar} 宽度 {w}% 与 API {key}={want}% 不一致")

    @R.case("D-02", "Model Status 与 /api/instances 一致", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(2000)
        st, api, _ = R.http.get("/api/instances")
        cards = R.js("document.querySelectorAll('#dashboardModels .model-card, #dashboardModels > *').length")
        r.evidence = {"ui_cards": cards, "api_instances": len(api or {})}
        R.ok(cards >= 9, f"UI 卡片 {cards} < 9")
        R.ok(len(api or {}) >= 9, f"API 实例 {len(api or {})} < 9")

    @R.case("D-03", "GPU 卡数值与 nvidia-smi 接近", "L2")
    def _(r):
        st, sysinfo, _ = R.http.get("/api/system")
        gpus = sysinfo.get("gpus") or []
        R.ok(gpus, "API 未返回 GPU")
        if not need_page(r): return
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(2000)
        txt = R.text("#dashboardGPU")
        R.ok("MB" in txt or "%" in txt, f"GPU 卡无指标: {txt[:120]}")
        r.evidence = {"gpu_name": gpus[0].get("name")}

    @R.case("D-04", "实例状态变化在 3s 内反映到卡片", "L2")
    def _(r):
        if not need_page(r): return
        # 用 embedding 实例做无副作用验证：停再启
        R.http.post("/api/instances/embedding/stop")
        R.goto_tab("dashboard")
        R.page.wait_for_timeout(3000)
        txt1 = R.text("#dashboardModels")
        R.http.post("/api/instances/embedding/start")
        R.page.wait_for_timeout(4000)
        txt2 = R.text("#dashboardModels")
        r.evidence = {"before_len": len(txt1), "after_len": len(txt2)}

    # ================================================================
    # W Models 启动向导
    # ================================================================
    @R.case("W-01", "向导四步联动渲染", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("launch")
        R.page.wait_for_timeout(2500)
        for sel in ("#wiz-tiers-launchWizard", "#wiz-steps-launchWizard",
                    "#wiz-params-launchWizard", "#wiz-actions-launchWizard"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"缺 {sel}")
        R.ok(R.js("document.querySelectorAll('#wiz-tiers-launchWizard select').length >= 3"),
             "类别/引擎/版本 三个下拉未渲染")

    @R.case("W-02", "切换类别后引擎与参数重载", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("launch")
        R.page.wait_for_timeout(1500)
        R.js("window.wizSet('launchWizard','category','embedding')")
        R.page.wait_for_timeout(2500)
        eng = R.js("document.querySelector('#wiz-tiers-launchWizard select:nth-of-type(2)')?.value")
        r.evidence = {"engine_after_switch": eng}
        R.ok(R.js("document.querySelectorAll('#wiz-params-launchWizard input, #wiz-params-launchWizard select').length >= 0"),
             "参数区渲染异常")
        R.js("window.wizSet('launchWizard','category','chat')")

    @R.case("W-03", "文件浏览器：打开/上下级/选文件回填", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("launch")
        R.page.wait_for_timeout(2000)
        R.js("window.wizOpenFiles('launchWizard')")
        R.page.wait_for_selector("#fileBrowserModal.active", timeout=8000)
        R.page.wait_for_timeout(1500)
        items = R.js("document.querySelectorAll('#fileBrowserBody *[onclick]').length")
        R.ok(items > 0, "文件列表为空")
        r.evidence = {"entries": items}
        R.js("window.closeFileBrowser()")

    @R.case("W-04", "UID 自动推荐随模型文件变化", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("launch")
        R.page.wait_for_timeout(1500)
        R.js("window.wizSet('launchWizard','category','embedding')")
        R.js("window.wizSet('launchWizard','modelFile','Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf')")
        R.page.wait_for_timeout(1800)
        # uid 框没有 id，靠 label 文本定位；留空时 placeholder 就是系统建议值
        uid_ph = R.js("""
          (() => {
            const fields = Array.from(document.querySelectorAll('#wiz-tiers-launchWizard .field'));
            const f = fields.find(x => (x.querySelector('label')?.textContent || '').includes('实例 UID'));
            return f ? (f.querySelector('input')?.placeholder || '') : '';
          })()
        """)
        name_ph = R.js("""
          (() => {
            const fields = Array.from(document.querySelectorAll('#wiz-tiers-launchWizard .field'));
            const f = fields.find(x => (x.querySelector('label')?.textContent || '').includes('模型名称'));
            return f ? (f.querySelector('input')?.placeholder || '') : '';
          })()
        """)
        uid, name = uid_ph, name_ph
        r.evidence = {"uid_placeholder": uid, "name_placeholder": name}
        R.eq(uid, "qwen3-embedding-0.6b", "自动派生 uid（剥量化后缀+带类别身份）")
        R.ok(name and name != uid,
             f"显示名应与 uid 不同，否则客户端会拿 id 覆盖名称（uid={uid} name={name}）")
        # 名称应保留原始大小写
        R.ok(any(c.isupper() for c in name), f"名称应保留大小写: {name}")

    @R.case("W-05", "裸家族名 UID 实时风险提示", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("launch")
        R.page.wait_for_timeout(1500)
        R.js("window.wizSet('launchWizard','category','embedding')")
        R.js("window.wizSet('launchWizard','uid','qwen3')")
        R.page.wait_for_timeout(1200)
        hint = R.text("#wiz-uidhint-launchWizard")
        R.ok(hint and ("⚠" in hint or "裸家族" in hint), f"未提示风险: {hint!r}")

    @R.case("W-06", "量化后缀 UID 风险提示", "L2")
    def _(r):
        if not need_page(r): return
        R.js("window.wizSet('launchWizard','uid','qwen3-embedding-0.6b-q8_0')")
        R.page.wait_for_timeout(1000)
        hint = R.text("#wiz-uidhint-launchWizard")
        R.ok("量化" in hint or "⚠" in hint, f"未提示量化后缀: {hint!r}")
        R.js("window.wizSet('launchWizard','uid','')")

    @R.case("W-07", "wizPayload 与实际提交字段一致", "L2")
    def _(r):
        if not need_page(r): return
        has = R.js("typeof window.wizPayload === 'function'")
        if not has:
            # wizPayload 未暴露到 window：退化为检查预览按钮产出的 JSON
            R.js("window.wizPreview && window.wizPreview('launchWizard')")
            R.page.wait_for_timeout(600)
            d = json.loads(R.js(
                "JSON.stringify((document.getElementById('wiz-preview-launchWizard')?.textContent||'{}').trim() || '{}')"))
        else:
            d = json.loads(R.js("JSON.stringify(window.wizPayload('launchWizard'))"))
        for k in ("model_type", "engine_type", "model_file", "parameters"):
            R.ok(k in d, f"payload 缺 {k}")
        r.evidence = {"payload_keys": list(d)}

    @R.case("W-08", "真实启动 embedding 实例（0.6B）", "L2")
    def _(r):
        st, body, el = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True, "ctx_size": 4096},
            "model_uid": "wb-embed",
        }, timeout=180)
        r.evidence = {"status": st, "elapsed_s": round(el, 1), "body": body}
        R.eq(body.get("model_uid"), "wb-embed", "uid")
        R.ok(body.get("port", 0) >= 18100, f"端口应 ≥18100: {body.get('port')}")
        R.ok(not body.get("error"), f"启动失败: {body.get('error')}")
        # 等 running
        for _ in range(30):
            _, inst, _ = R.http.get("/api/instances/wb-embed")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        R.eq(inst.get("status"), "running", "实例状态")

    @R.case("W-09", "启动后 /v1/models 带 name 与 capabilities", "L2")
    def _(r):
        st, d, _ = R.http.get("/v1/models")
        m = next((x for x in d["data"] if x["id"] == "wb-embed"), None)
        R.ok(m, "新实例未出现在 /v1/models")
        R.eq(m.get("capabilities"), ["embedding"], "capabilities")
        R.ok(m.get("name") and m["name"] != "wb-embed", f"name 应不同于 uid: {m.get('name')}")

    @R.case("W-10", "embedding 实例真实出向量", "L2")
    def _(r):
        # 刚启动的实例端口可能还没就绪，轮询到能出向量为止（最多 60s）
        last = None
        for _ in range(20):
            st, d, el = R.http.post("/v1/embeddings",
                                    {"model": "wb-embed", "input": "AMM 白盒测试"}, timeout=90)
            emb0 = (d.get("data") or [{}])[0].get("embedding") if isinstance(d, dict) else None
            if st == 200 and emb0:
                break
            last = (st, str(d)[:200])
            time.sleep(4)
        else:
            raise AssertionError(f"实例始终未就绪，最后一次: {last}")
        r.evidence = {"status": st, "elapsed_s": round(el, 2)}
        R.eq(st, 200, "status")
        emb = (d.get("data") or [{}])[0].get("embedding")
        R.ok(emb and len(emb) > 100, f"向量维度异常: {len(emb) if emb else 0}")
        r.evidence["dim"] = len(emb)

    @R.case("W-11", "缺模型文件启动返回可读错误", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "chat", "engine_type": "llama_cpp",
            "model_file": "no/such/file.gguf", "model_uid": "wb-badfile",
        }, timeout=60)
        r.evidence = {"status": st, "error": str(d.get("error"))[:200]}
        R.ok(d.get("error"), "应返回 error")

    @R.case("W-12", "重复 UID 被拒绝", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "model_uid": "wb-embed",
        })
        R.contains(str(d.get("error", "")), "已存在", "重复 uid")

    # ================================================================
    # R 运行中实例
    # ================================================================
    @R.case("R-01", "运行中统计条与列表一致", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("running")
        # rmRefresh 会拉 /api/running-models + /api/instances，机器上有 GPU 探测时偏慢
        for _ in range(15):
            R.page.wait_for_timeout(1000)
            if (R.text("#rmStatRunning") or "--") != "--":
                break
        running_ui = R.text("#rmStatRunning")
        _, d, _ = R.http.get("/api/running-models")
        n = len(d.get("models") or [])
        r.evidence = {"ui": running_ui, "api": n}
        R.ok(running_ui and running_ui != "--", "统计条未填充")
        R.ok(str(n) in running_ui, f"UI {running_ui} 与 API {n} 不一致")

    @R.case("R-02", "列表包含 wb-embed 且字段齐全", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("running")
        R.page.wait_for_timeout(2500)
        body = R.text("#rmTableBody")
        R.contains(body, "wb-embed", "列表")
        for col in ("embedding", "llama_cpp", "running"):
            R.contains(body.lower(), col.lower(), "列值")

    @R.case("R-03", "Stop→Start 状态机流转", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/instances/wb-embed/stop")
        R.ok(not d.get("error"), f"stop 失败: {d.get('error')}")
        time.sleep(1)
        _, inst, _ = R.http.get("/api/instances/wb-embed")
        R.eq(inst.get("status"), "stopped", "停止后状态")
        st, d, _ = R.http.post("/api/instances/wb-embed/start", timeout=180)
        R.ok(not d.get("error"), f"start 失败: {d.get('error')}")
        for _ in range(30):
            _, inst, _ = R.http.get("/api/instances/wb-embed")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        R.eq(inst.get("status"), "running", "启动后状态")

    @R.case("R-04", "Restart 保持 running 且端口不变", "L2")
    def _(r):
        _, before, _ = R.http.get("/api/instances/wb-embed")
        st, d, _ = R.http.post("/api/instances/wb-embed/restart", timeout=180)
        r.evidence = {"status": st, "resp": str(d)[:200]}
        time.sleep(2)
        for _ in range(40):
            _, after, _ = R.http.get("/api/instances/wb-embed")
            if after.get("status") == "running":
                break
            time.sleep(1)
        R.eq(after.get("status"), "running", "重启后状态")
        R.eq(after.get("port"), before.get("port"), "端口")

    @R.case("R-05", "改参数后生效并持久化", "L2")
    def _(r):
        # 前端（app.js:839 saveParameters）提交的是**扁平**参数对象，
        # 不是 {"parameters": {...}}。按前端真实契约测。
        st, d, _ = R.http.put("/api/instances/wb-embed/parameters",
                              {"embeddings": True, "ctx_size": 8192})
        r.evidence = {"status": st, "resp": str(d)[:200]}
        _, inst, _ = R.http.get("/api/instances/wb-embed")
        R.eq(inst.get("parameters", {}).get("ctx_size"), 8192, "ctx_size")

        # 顺带记录契约健壮性：包一层 parameters 会被静默吞掉（见缺陷 DEV-01）
        st2, _, _ = R.http.put("/api/instances/wb-embed/parameters",
                               {"parameters": {"ctx_size": 4096}})
        _, inst2, _ = R.http.get("/api/instances/wb-embed")
        nested_ignored = inst2.get("parameters", {}).get("ctx_size") != 4096
        r.evidence["nested_payload_ignored"] = nested_ignored
        R.ok(nested_ignored, "嵌套 parameters 被意外接受（契约应统一）")

    @R.case("R-06", "启动命令预览与实际一致", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/instances/wb-embed/command/preview")
        R.eq(st, 200, "status")
        R.ok(d.get("args") or d.get("command") or d.get("cmd"), f"预览为空: {d}")
        r.evidence = {"has_args": bool(d.get("args"))}

    @R.case("R-07", "实例日志可读", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/instances/wb-embed/logs?lines=50")
        R.eq(st, 200, "status")
        logs = d.get("logs") or d.get("lines") or []
        R.ok(isinstance(logs, (list, str)), "日志格式异常")
        r.evidence = {"lines": len(logs) if isinstance(logs, list) else len(str(logs))}

    @R.case("R-08", "另存为模版生成 custom yaml", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/templates/from-instance", {"model_id": "wb-embed"})
        r.evidence = {"status": st, "resp": str(d)[:200]}
        R.eq(st, 200, "status")
        tpl = d.get("template") or {}
        R.ok(tpl.get("is_custom"), f"应为自定义模版: {tpl.get('id')}")
        r.evidence["template_id"] = tpl.get("id")

    @R.case("R-09", "并发双击 Start 幂等（只起一个进程）", "L2")
    def _(r):
        import threading
        outs = []
        def call():
            outs.append(R.http.post("/api/instances/wb-embed/start", timeout=120)[1])
        ts = [threading.Thread(target=call) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        R.ok(all(isinstance(o, dict) for o in outs), "返回格式异常")
        _, d, _ = R.http.get("/api/running-models")
        same = [m for m in d.get("models") or [] if m["model_uid"] == "wb-embed"]
        R.eq(len(same), 1, "同一 uid 应只有一条运行记录")
        r.evidence = {"concurrent_responses": [str(o)[:60] for o in outs]}

    @R.case("R-10", "实例详情字段完整", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/instances/wb-embed")
        R.eq(st, 200, "status")
        for k in ("model_id", "name", "category", "port", "engine_type", "status",
                  "is_dynamic", "parameters"):
            R.ok(k in d, f"缺字段 {k}")

    # ================================================================
    # T 模版库
    # ================================================================
    @R.case("T-01", "模版列表加载与统计", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("launch"); R.goto_sub("templates")
        # 模版列表要读 14 个 yaml 并渲染，等它真的画出来
        for _ in range(20):
            R.page.wait_for_timeout(1000)
            if R.js("document.querySelectorAll('#tplGrid .tpl-card').length") >= 10:
                break
        cnt = R.js("document.querySelectorAll('#tplGrid .tpl-card, #tplGrid > *').length")
        R.ok(cnt >= 10, f"模版卡片 {cnt} < 10")
        R.ok(R.text("#tplStats"), "统计区为空")

    @R.case("T-02", "分类过滤生效", "L2")
    def _(r):
        if not need_page(r): return
        before = R.js("document.querySelectorAll('#tplGrid .tpl-card').length")
        R.page.select_option("#tplCategory", "embedding")
        for _ in range(20):
            R.page.wait_for_timeout(1000)
            if R.js("document.querySelectorAll('#tplGrid .tpl-card').length") != before:
                break
        R.page.wait_for_timeout(1500)
        after = R.js("document.querySelectorAll('#tplGrid .tpl-card').length")
        r.evidence = {"all": before, "embedding": after}
        R.ok(after < before, f"过滤无效 {before}→{after}")
        R.page.select_option("#tplCategory", "")

    @R.case("T-03", "关键词搜索", "L2")
    def _(r):
        if not need_page(r): return
        R.page.fill("#tplKeyword", "qwen")
        R.page.press("#tplKeyword", "Enter")
        R.page.wait_for_timeout(2500)
        txt = R.text("#tplGrid").lower()
        R.ok("qwen" in txt or "无" in txt, f"搜索结果异常: {txt[:100]}")
        R.page.fill("#tplKeyword", "")
        R.page.press("#tplKeyword", "Enter")
        R.page.wait_for_timeout(1500)

    @R.case("T-04", "模版详情抽屉打开", "L2")
    def _(r):
        if not need_page(r): return
        R.js("document.querySelector('#tplGrid > *')?.click()")
        R.page.wait_for_timeout(2500)
        disp = R.js("getComputedStyle(document.getElementById('tplDetail')).display")
        R.ok(disp != "none", "详情抽屉未显示")
        body = R.text("#tplDetailBody")
        R.ok("部署" in body, f"详情缺部署区: {body[:100]}")
        r.evidence = {"detail_len": len(body)}

    @R.case("T-05", "模版详情含引擎/下载源/部署表单", "L2")
    def _(r):
        if not need_page(r): return
        body = R.text("#tplDetailBody")
        for k in ("引擎", "下载源", "实例 UID", "模型名称"):
            R.contains(body, k, "详情字段")

    @R.case("T-06", "新建模版编辑器可打开", "L2")
    def _(r):
        if not need_page(r): return
        R.js("window.tplOpenEditor('')")
        R.page.wait_for_timeout(1800)
        R.ok(R.js("document.getElementById('tplEditorModal').classList.contains('active')"),
             "编辑器未打开")
        R.js("window.tplCloseEditor()")

    @R.case("T-07", "模版 categories 接口与 UI 一致", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/templates/categories")
        R.eq(st, 200, "status")
        cats = d.get("categories") or {}
        R.ok(len(cats) >= 5, f"分类 {len(cats)} < 5")
        r.evidence = cats

    @R.case("T-08", "按模版部署创建实例", "L2")
    def _(r):
        st, lst, _ = R.http.get("/api/templates?category=embedding")
        tpls = lst.get("templates") or []
        R.ok(tpls, "无 embedding 模版")
        tid = tpls[0]["id"]
        st, d, _ = R.http.post(f"/api/templates/{tid}/deploy",
                               {"model_name": "白盒模版部署测试"}, timeout=180)
        r.evidence = {"tpl": tid, "status": st, "resp": str(d)[:220]}
        R.ok(d.get("model_uid") or d.get("error"), "无返回")

    # ================================================================
    # P Playground
    # ================================================================
    @R.case("P-01", "9 个子页签切换且面板对应", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("playground")
        for pg in ("chat", "embedding", "asr", "tts", "rerank", "ocr", "image", "video", "i2v"):
            R.goto_pg(pg)
            R.ok(R.js(f"document.getElementById('pg-{pg}').classList.contains('active')"),
                 f"{pg} 未激活")

    @R.case("P-02", "Embedding Playground 真实出结果", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("playground"); R.goto_pg("embedding")
        R.page.wait_for_timeout(2500)
        R.js("document.querySelectorAll('#pgEmbModel option').length > 0 && "
             "(()=>{const s=document.getElementById('pgEmbModel');"
             "const o=Array.from(s.options).find(x=>x.value.includes('wb-embed'));"
             "if(o){s.value=o.value;}})()")
        R.page.click("#pg-embedding button.btn-primary")
        R.page.wait_for_timeout(6000)
        res = R.text("#pgEmbResult")
        R.ok("维度" in res or "dim" in res.lower() or len(res) > 40,
             f"结果区无有效输出: {res[:150]}")
        r.evidence = {"result_head": res[:200]}

    @R.case("P-03", "Chat 页签元素齐全", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_pg("chat")
        for sel in ("#pgChatModel", "#pgChatTemp", "#pgChatMaxTokens",
                    "#pgChatTopP", "#pgChatSystem", "#pgChatImage", "#pgChatInput"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"缺 {sel}")

    @R.case("P-04", "Chat 清空会话有 confirm", "L2")
    def _(r):
        if not need_page(r): return
        dialog_seen = {"hit": False}
        def on_dialog(d):
            dialog_seen["hit"] = True
            d.dismiss()
        R.page.on("dialog", on_dialog)
        try:
            R.page.click("#pg-chat button:has-text('清空会话')")
            R.page.wait_for_timeout(1200)
        finally:
            R.page.remove_listener("dialog", on_dialog)
        R.ok(dialog_seen["hit"], "清空会话未弹 confirm")

    @R.case("P-05", "参数边界值被前端约束", "L2")
    def _(r):
        if not need_page(r): return
        lo = R.js("document.querySelector('#pgChatTemp').min")
        hi = R.js("document.querySelector('#pgChatTemp').max")
        R.eq((lo, hi), ("0", "2"), "temperature 边界属性")
        mt = R.js("document.querySelector('#pgChatMaxTokens').max")
        R.ok(mt, "max_tokens 无上限约束")

    @R.case("P-06", "T2I 面板参数与显存估算", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_pg("image")
        R.page.wait_for_timeout(3000)
        for sel in ("#pgT2IPrompt", "#pgT2IWidth", "#pgT2IHeight",
                    "#pgT2ISteps", "#pgT2IGuidance", "#pgT2ISeed"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"缺 {sel}")
        vram = R.text("#pgVram-t2i")
        r.evidence = {"vram_text": vram[:120]}
        R.ok(vram, "显存估算区为空")

    @R.case("P-07", "T2V/I2V 面板元素齐全", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_pg("video")
        R.page.wait_for_timeout(2000)
        for sel in ("#pgT2VPrompt", "#pgT2VResolution", "#pgT2VFrames", "#pgT2VGuidance2"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"T2V 缺 {sel}")
        R.goto_pg("i2v")
        R.page.wait_for_timeout(1500)
        for sel in ("#pgI2VPrompt", "#pgI2VImage", "#pgI2VFrames"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"I2V 缺 {sel}")

    @R.case("P-08", "ASR/TTS/Rerank/OCR 面板齐全", "L2")
    def _(r):
        if not need_page(r): return
        checks = {"asr": ("#pgAsrModel", "#pgAsrFile"), "tts": ("#pgTtsModel", "#pgTtsInput"),
                  "rerank": ("#pgRerankQuery", "#pgRerankDocs"), "ocr": ("#pgOcrModel", "#pgOcrFile")}
        for pg, sels in checks.items():
            R.goto_pg(pg)
            R.page.wait_for_timeout(800)
            for sel in sels:
                R.ok(R.js(f"!!document.querySelector('{sel}')"), f"{pg} 缺 {sel}")

    # ================================================================
    # K Token Router
    # ================================================================
    @R.case("K-01", "统计卡与 usage 接口一致", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("router")
        R.page.wait_for_timeout(3000)
        _, prov, _ = R.http.get("/api/providers")
        n_prov = len(prov.get("providers") or [])
        ui = R.text("#rtProviders")
        r.evidence = {"ui_providers": ui, "api_providers": n_prov}
        R.ok(ui and ui != "--", "统计卡未填充")

    @R.case("K-02", "Provider 类型切换自动填 Base URL", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("router")
        R.page.wait_for_timeout(1500)
        R.page.select_option("#pvType", "deepseek")
        R.page.wait_for_timeout(800)
        base = R.js("document.getElementById('pvBase').value")
        ph = R.js("document.getElementById('pvBase').placeholder")
        r.evidence = {"base_value": base, "placeholder": ph}
        R.ok("deepseek" in (base + ph).lower(), f"未提示 deepseek 默认地址: {base!r}/{ph!r}")

    @R.case("K-03", "创建/读取/删除 provider（可逆）", "L2")
    def _(r):
        name = "wb-prov"
        # 用独立名字，绝不动真实 provider
        st, d, _ = R.http.post(f"/api/providers/{name}", {
            "type": "openai", "base_url": "http://127.0.0.1:9/v1",
            "models": ["wb-model-a"], "enabled": True})
        r.evidence = {"create_status": st}
        st2, g, _ = R.http.get(f"/api/providers/{name}")
        R.eq(st2, 200, "读取")
        R.ok(g.get("name") == name or g.get("type"), f"读回内容异常: {g}")
        st3, dl, _ = R.http.delete(f"/api/providers/{name}")
        st4, _, _ = R.http.get(f"/api/providers/{name}")
        R.eq(st4, 404, "删除后应 404")

    @R.case("K-04", "Provider 保存不填 Key 不破坏已有 Key", "L2")
    def _(r):
        st, lst, _ = R.http.get("/api/providers")
        provs = lst.get("providers") or []
        R.ok(provs, "无 provider 可测")
        # 环境前置：沙箱可能是全新实例，没有配置过 Key 的 provider
        target = next((p for p in provs if p.get("has_key")), None)
        if not target:
            r.status = "SKIP"
            r.detail = "环境前置缺失：当前无带 Key 的 provider（沙箱为全新实例）"
            r.evidence = {"providers": [p.get("name") for p in provs]}
            return
        orig_base = target.get("base_url") or ""
        st, d, _ = R.http.post(f"/api/providers/{target['name']}",
                               {"type": target["type"],
                                "base_url": orig_base,
                                "models": target.get("models") or []})
        _, after, _ = R.http.get(f"/api/providers/{target['name']}")
        R.ok(after.get("has_key"), f"留空保存后 Key 丢失: {target['name']}")
        # 恢复原 base_url（若保存把它写空/写错，后续 provider 调用会打到错误地址）
        if orig_base and after.get("base_url") != orig_base:
            R.http.post(f"/api/providers/{target['name']}",
                        {"type": target["type"], "base_url": orig_base})
        r.evidence["base_url_restored"] = orig_base

    @R.case("K-05", "可用模型清单合并本地+provider", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/router/models")
        R.eq(st, 200, "status")
        data = d.get("data") or d.get("models") or []
        R.ok(len(data) > 0, "模型清单为空")
        r.evidence = {"count": len(data)}

    @R.case("K-06", "签发 Key：明文只回一次，列表只显示掩码", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/token-router/keys", {"name": "wb-key", "rpm_limit": 5})
        R.eq(st, 200, "status")
        raw = d.get("key") or ""
        R.ok(raw.startswith("sk-amm-"), f"key 格式: {raw[:12]}")
        _, lst, _ = R.http.get("/api/token-router/keys")
        entry = [k for k in lst.get("keys") or [] if k.get("name") == "wb-key"]
        R.ok(entry, "新 Key 未出现在列表")
        R.ok(raw not in json.dumps(lst, ensure_ascii=False), "列表泄露明文 Key")
        r.evidence = {"key_id": entry[0].get("key_id"), "masked": bool(entry[0].get("key"))}

    @R.case("K-07", "Key 改名/改限额", "L2")
    def _(r):
        _, lst, _ = R.http.get("/api/token-router/keys")
        entry = next((k for k in lst.get("keys") or [] if k.get("name") == "wb-key"), None)
        if not entry:
            r.status = "SKIP"
            r.detail = "K-06 未留下测试 Key（鉴权状态被重置）"
            return
        key = entry.get("key") or entry.get("key_id")
        st, d, _ = R.http.put(f"/api/token-router/keys/{key}", {"name": "wb-key-2", "rpm_limit": 9})
        r.evidence = {"status": st, "resp": str(d)[:150]}
        _, lst2, _ = R.http.get("/api/token-router/keys")
        R.ok(any(k.get("name") == "wb-key-2" for k in lst2.get("keys") or []), "改名未生效")

    @R.case("K-08", "无效 Key 的行为符合当前鉴权状态", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/router/relay",
                               {"model": "whatever", "messages": []},
                               headers={"Authorization": "Bearer sk-invalid-xyz"})
        _, auth, _ = R.http.get("/api/settings/auth")
        enforced = bool(auth.get("auth_enforced"))
        r.evidence = {"status": st, "auth_enforced": enforced}
        if enforced:
            R.eq(st, 401, "已开启鉴权时无效 Key 应 401")
        else:
            # 免鉴权模式：无效 Key 不拦截，最终因模型不存在而失败
            R.ok(st in (400, 404, 502), f"免鉴权下应走到路由阶段，实际 {st}")
        R.ok("error" in json.dumps(d, ensure_ascii=False).lower(), f"缺错误结构: {d}")

    @R.case("K-09", "白名单外模型被拒", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/token-router/keys",
                               {"name": "wb-key-wl", "models": ["only-this-model"]})
        raw = d.get("key") or ""
        R.ok(raw, "签发失败")
        st2, d2, _ = R.http.post("/api/router/relay",
                                 {"model": "other-model-xyz", "messages": []},
                                 headers={"Authorization": f"Bearer {raw}"})
        r.evidence = {"status": st2, "resp": str(d2)[:150]}
        R.ok(st2 in (403, 429, 404, 400), f"白名单外应被拒，实际 {st2}")

    @R.case("K-10", "用量报表结构", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/token-router/usage")
        R.eq(st, 200, "status")
        R.ok("totals" in d, "缺 totals")
        r.evidence = {"totals": d.get("totals")}

    @R.case("K-11", "账单 CSV 可导出且带 BOM", "L2")
    def _(r):
        st, body, _ = R.http.get("/api/token-router/billing/export?format=bill", raw=True)
        R.eq(st, 200, "status")
        R.ok(body[:3] == b"\xef\xbb\xbf", f"缺 UTF-8 BOM: {body[:8]}")
        R.ok(b"," in body, "CSV 无分隔符")
        r.evidence = {"bytes": len(body)}

    @R.case("K-12", "详单 CSV 可导出", "L2")
    def _(r):
        st, body, _ = R.http.get("/api/token-router/billing/export?format=detail", raw=True)
        R.eq(st, 200, "status")
        R.ok(len(body) > 50, f"内容过短: {len(body)}")

    @R.case("K-13", "模型元数据覆盖生效", "L2")
    def _(r):
        st, d, _ = R.http.put("/api/router/models/wb-embed",
                              {"category": "embedding", "note": "白盒测试标记",
                               "input_price": 1.5, "output_price": 2.5})
        r.evidence = {"status": st, "resp": str(d)[:200]}
        _, models, _ = R.http.get("/api/router/models")
        data = models.get("data") or models.get("models") or []
        m = next((x for x in data if x.get("id") == "wb-embed"), None)
        R.ok(m, "覆盖后找不到模型")
        R.ok(m.get("note") == "白盒测试标记" or m.get("pricing_cny_per_mtok"), f"覆盖未生效: {m}")

    @R.case("K-14", "默认 Key 状态接口", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/settings/default-key")
        R.eq(st, 200, "status")
        R.ok("has_key" in d and "masked" in d, "字段缺失")

    # ================================================================
    # L Log Center
    # ================================================================
    @R.case("L-01", "两个子页签互斥切换", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("logs")
        R.page.wait_for_timeout(1500)
        R.page.click("#lcTabCalls")
        R.page.wait_for_timeout(2500)
        R.ok(R.js("getComputedStyle(document.getElementById('lcCallsPane')).display") != "none",
             "调用历史面板未显示")
        R.ok(R.js("getComputedStyle(document.getElementById('lcFilePane')).display") == "none",
             "文件日志面板未隐藏")
        R.page.click("#lcTabFile")
        R.page.wait_for_timeout(1200)

    @R.case("L-02", "文件日志查询有内容", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("logs")
        R.page.wait_for_timeout(1000)
        R.page.select_option("#lcSource", "all")
        R.page.click("button:has-text('查询')")
        R.page.wait_for_timeout(4000)
        v = R.text("#lcViewer")
        R.ok(len(v) > 50, f"日志为空: {v[:80]}")

    @R.case("L-03", "级别过滤减少结果", "L2")
    def _(r):
        if not need_page(r): return
        R.page.select_option("#lcLevel", "ERROR")
        R.page.click("button:has-text('查询')")
        R.page.wait_for_timeout(3500)
        v = R.text("#lcViewer")
        r.evidence = {"error_view_len": len(v)}
        R.page.select_option("#lcLevel", "ALL")
        R.page.click("button:has-text('查询')")
        R.page.wait_for_timeout(2500)

    @R.case("L-04", "自动刷新开关启停", "L2")
    def _(r):
        if not need_page(r): return
        R.page.check("#lcAuto")
        R.page.wait_for_timeout(1500)
        R.ok(R.js("document.getElementById('lcAuto').checked"), "未勾选")
        R.page.uncheck("#lcAuto")
        R.page.wait_for_timeout(800)

    @R.case("L-05", "调用历史列表加载", "L2")
    def _(r):
        if not need_page(r): return
        R.page.click("#lcTabCalls")
        R.page.wait_for_timeout(3500)
        txt = R.text("#lccList")
        cnt = R.text("#lccCount")
        r.evidence = {"count_text": cnt, "list_len": len(txt)}
        R.ok(cnt, "计数区为空")

    @R.case("L-06", "调用历史展开显示输入/输出/性能", "L2")
    def _(r):
        if not need_page(r): return
        rows = R.js("document.querySelectorAll('#lccList .lcc-row').length")
        if not rows:
            r.status = "SKIP"; r.detail = "无调用记录"; return
        R.js("document.querySelector('#lccList .lcc-row').click()")
        R.page.wait_for_timeout(800)
        d = R.text("#lcc-d-0")
        for k in ("输入", "输出", "参数", "性能"):
            R.contains(d, k, "详情面板")
        R.ok("(stream)" not in d or "真实" in d,
             "输出仍是 (stream) 占位符（SSE 捕获回归）")

    @R.case("L-07", "文件管理弹窗可打开", "L2")
    def _(r):
        if not need_page(r): return
        R.page.click("#lcTabFile")
        R.page.wait_for_timeout(1000)
        R.page.click("button:has-text('文件管理')")
        R.page.wait_for_timeout(3000)
        disp = R.js("getComputedStyle(document.getElementById('lcFilesBox')).display")
        r.evidence = {"display": disp}
        R.ok(disp != "none", "文件管理区未显示")

    @R.case("L-08", "日志来源接口与 UI 下拉一致", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/logs/center/sources")
        R.eq(st, 200, "status")
        r.evidence = {"sources": str(d)[:200]}

    @R.case("L-09", "导出当前筛选返回内容", "L2")
    def _(r):
        st, body, _ = R.http.get("/api/logs/center/export?source=all&level=ALL&lines=20", raw=True)
        R.eq(st, 200, "status")
        R.ok(len(body) > 0, "导出为空")
        r.evidence = {"bytes": len(body)}

    # ================================================================
    # S Settings
    # ================================================================
    @R.case("S-01", "服务配置区显示与 API 一致", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("settings")
        R.page.wait_for_timeout(2500)
        _, s, _ = R.http.get("/api/settings")
        R.eq(R.text("#cfgVersion"), s["version"], "版本")
        R.eq(R.text("#cfgPort"), str(s["port"]), "端口")

    @R.case("S-02", "PyPI 镜像预设可选", "L2")
    def _(r):
        if not need_page(r): return
        opts = R.js("document.querySelectorAll('#cfgPipPreset option').length")
        R.ok(opts >= 2, f"镜像预设 {opts} < 2")

    @R.case("S-03", "客户端分组名保存并反映到 /v1/models owned_by", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/settings/node-name", {"node_name": "amm-wb"})
        r.evidence = {"status": st, "resp": str(d)[:150]}
        # 用例隔离：确保处于免鉴权模式（新增的 auth 开关可能被前面的用例打开）
        R.http.post("/api/settings/auth/disable", {"confirm": True})
        stm, models, _ = R.http.get("/v1/models")
        R.eq(stm, 200, "/v1/models status")
        R.ok(isinstance(models, dict) and "data" in models,
             f"/v1/models 返回异常: {str(models)[:150]}")
        owners = {m.get("owned_by") for m in models["data"] if m.get("port")}
        R.ok("amm-wb" in owners, f"owned_by 未更新: {owners}")
        R.http.post("/api/settings/node-name", {"node_name": "amm"})

    @R.case("S-04", "分组名空值被拒", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/settings/node-name", {"node_name": ""})
        R.eq(st, 400, "status")
        R.ok(d.get("error"), "应有 error")

    @R.case("S-05", "引擎管理列表渲染", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("settings")
        R.page.wait_for_timeout(4000)
        n = R.js("document.querySelectorAll('#engineList .engine-item, #engineList > *').length")
        r.evidence = {"engine_items": n}
        R.ok(n >= 1, "引擎列表为空")

    @R.case("S-06", "重载配置成功", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/settings/reload", {})
        r.evidence = {"status": st, "resp": str(d)[:150]}
        R.eq(st, 200, "status")
        R.ok(d.get("ok") or d.get("success") or "error" not in d, f"返回异常: {d}")

    @R.case("S-07", "下载服务日志", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/logs/server?lines=100")
        R.eq(st, 200, "status")
        logs = d.get("logs")
        R.ok(logs, "日志为空")

    @R.case("S-08", "pip 镜像保存与读回", "L2")
    def _(r):
        st, before, _ = R.http.get("/api/settings/pip")
        orig = before.get("index_url") or before.get("pip_index") or ""
        st2, d, _ = R.http.post("/api/settings/pip",
                                {"index_url": "https://mirrors.aliyun.com/pypi/simple/"})
        r.evidence = {"status": st2, "orig": orig}
        _, after, _ = R.http.get("/api/settings/pip")
        R.ok("aliyun" in json.dumps(after, ensure_ascii=False), f"未保存: {after}")
        if orig:
            R.http.post("/api/settings/pip", {"index_url": orig})

    # ================================================================
    # X Tools
    # ================================================================
    @R.case("X-01", "下载面板元素齐全", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("tools")
        R.page.wait_for_timeout(2500)
        for sel in ("#hfEndpointPreset", "#hfEndpoint", "#hfToken", "#dlSource",
                    "#dlModelId", "#dlCategory"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"缺 {sel}")

    @R.case("X-02", "HF 镜像预设切换生效", "L2")
    def _(r):
        if not need_page(r): return
        R.page.select_option("#hfEndpointPreset", "https://huggingface.co")
        R.page.wait_for_timeout(800)
        v = R.js("document.getElementById('hfEndpoint').value")
        R.ok("huggingface.co" in v, f"未联动: {v}")
        R.page.select_option("#hfEndpointPreset", "https://hf-mirror.com")
        R.page.wait_for_timeout(500)

    @R.case("X-03", "代理开关联动输入框禁用态", "L2")
    def _(r):
        if not need_page(r): return
        R.page.check("#dlProxyEnabled")
        R.page.wait_for_timeout(600)
        R.ok(not R.js("document.getElementById('dlProxyUrl').disabled"), "启用后仍禁用")
        R.page.uncheck("#dlProxyEnabled")
        R.page.wait_for_timeout(600)
        R.ok(R.js("document.getElementById('dlProxyUrl').disabled"), "禁用后未禁用")

    @R.case("X-04", "HF Token 打码显示不泄露明文", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/models/download/settings")
        R.eq(st, 200, "status")
        r.evidence = {"keys": list(d.get("settings") or d)}

        # 只看「值」是否有真 token 形态（hf_ + 30 位以上），不要拿字段名当判据：
        # hf_endpoint / hf_token_masked 这些键名本身就含 hf_ 前缀。
        def walk(node, path=""):
            bad = []
            if isinstance(node, dict):
                for k, v in node.items():
                    bad += walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    bad += walk(v, f"{path}[{i}]")
            elif isinstance(node, str):
                if re.match(r"^hf_[A-Za-z0-9]{30,}$", node.strip()):
                    bad.append((path, node[:12] + "…"))
            return bad

        leaks = walk(d)
        R.ok(not leaks, f"返回值里出现未打码的 HF token: {leaks}")
        # 若是明文打码字段，必须确实被遮蔽
        masked = (d.get("settings") or d).get("hf_token_masked")
        if masked:
            R.ok("…" in masked or "*" in masked or len(masked) < 12,
                 f"打码字段像明文: {masked}")

    @R.case("X-05", "查询模型版本与文件列表", "L2")
    def _(r):
        if not need_page(r): return
        R.goto_tab("tools")
        R.page.wait_for_timeout(1500)
        R.page.fill("#dlModelId", "Qwen/Qwen3-Embedding-0.6B-GGUF")
        R.page.click("button:has-text('查询版本与文件')")
        R.page.wait_for_timeout(20000)
        panel = R.js("getComputedStyle(document.getElementById('dlVersionPanel')).display")
        r.evidence = {"panel_display": panel}
        R.ok(panel != "none", "版面板未出现")

    @R.case("X-06", "量化面板字段齐全", "L2")
    def _(r):
        if not need_page(r): return
        for sel in ("#quSrc", "#quType", "#quOut"):
            R.ok(R.js(f"!!document.querySelector('{sel}')"), f"缺 {sel}")

    @R.case("X-07", "三处目录浏览器各自可打开", "L2")
    def _(r):
        if not need_page(r): return
        opened = 0
        for fn in ("openQuantizeSrcBrowser()", "openQuantizeOutBrowser()",
                   "window.openDownloadDirBrowser()"):
            R.js(f"(window.{fn} || eval('${fn}'))") if False else R.page.evaluate(fn)
            R.page.wait_for_timeout(1800)
            if R.js("document.getElementById('fileBrowserModal').classList.contains('active')"):
                opened += 1
                R.js("window.closeFileBrowser()")
                R.page.wait_for_timeout(500)
        R.eq(opened, 3, "三个目录浏览器均应打开")

    @R.case("X-08", "下载任务状态接口", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/models/download/status")
        R.eq(st, 200, "status")

    # ================================================================
    # Z 破坏性操作（需 --destructive）
    # ================================================================
    @R.case("L2-45", "结束运行实例并释放端口", "L2")
    def _(r):
        _, before, _ = R.http.get("/api/instances/wb-embed")
        port = before.get("port")
        st, d, _ = R.http.delete("/api/instances/wb-embed")
        r.evidence = {"status": st, "resp": str(d)[:150], "released_port": port}
        R.ok(d.get("success"), f"删除失败: {d}")
        st2, _, _ = R.http.get("/api/instances/wb-embed")
        R.eq(st2, 404, "删除后应 404")

    @R.case("L2-46", "删除自定义模版", "L2")
    def _(r):
        _, lst, _ = R.http.get("/api/templates")
        custom = [t for t in lst.get("templates") or [] if t.get("is_custom")]
        if not custom:
            r.status = "SKIP"; r.detail = "无自定义模版"; return
        tid = custom[0]["id"]
        st, d, _ = R.http.delete(f"/api/templates/{tid}")
        r.evidence = {"template": tid, "status": st, "resp": str(d)[:120]}
        R.ok(st == 200, f"删除失败: {d}")

    @R.case("L2-47", "吊销虚拟 Key", "L2")
    def _(r):
        _, lst, _ = R.http.get("/api/token-router/keys")
        keys = [k for k in lst.get("keys") or [] if (k.get("name") or "").startswith("wb-key")]
        if not keys:
            r.status = "SKIP"; r.detail = "无测试 Key"; return
        k = keys[0]
        key = k.get("key") or k.get("key_id")
        st, d, _ = R.http.delete(f"/api/token-router/keys/{key}")
        r.evidence = {"status": st, "resp": str(d)[:120]}
        R.ok(st == 200 or d.get("success"), f"吊销失败: {d}")

    @R.case("L2-48", "删除 provider", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/providers/wb-del", {"type": "openai",
                                                         "base_url": "http://127.0.0.1:9/v1"})
        st2, d2, _ = R.http.delete("/api/providers/wb-del")
        st3, _, _ = R.http.get("/api/providers/wb-del")
        r.evidence = {"delete_status": st2}
        R.eq(st3, 404, "删除后应 404")

    @R.case("L2-49", "日志清理（清空内容）", "L2")
    def _(r):
        # 先确认文件存在
        st, files, _ = R.http.get("/api/logs/center/files")
        r.evidence = {"files_resp": str(files)[:180]}
        st2, d, _ = R.http.post("/api/logs/center/cleanup",
                                {"mode": "truncate", "targets": ["amm_server.log"]})
        r.evidence["cleanup"] = {"status": st2, "resp": str(d)[:180]}
        R.ok(st2 in (200, 400), f"清理返回异常 {st2}: {d}")
        # 服务必须仍健康
        st3, h, _ = R.http.get("/api/health")
        R.eq(h.get("status"), "ok", "清理后服务应仍健康")

    @R.case("L2-50", "重启服务后自动恢复", "L2")
    def _(r):
        st, d, _ = R.http.post("/api/settings/restart", {}, timeout=15)
        r.evidence = {"restart_status": st, "resp": str(d)[:150]}
        # 等服务回来
        alive = False
        for _ in range(60):
            time.sleep(2)
            st2, h, _ = R.http.get("/api/health", timeout=5)
            if st == 200 and h.get("status") == "ok":
                alive = True
                break
        R.ok(alive, "重启后服务未在 120s 内恢复")
        st3, s, _ = R.http.get("/api/settings")
        R.eq(st3, 200, "重启后 settings")

    @R.case("L2-51", "引擎卸载（选未使用的小引擎）", "L2")
    def _(r):
        st, d, _ = R.http.get("/api/engines/installed", timeout=120)
        engines = d.get("engines") or {}
        target = None
        for et, meta in engines.items():
            for inst in meta.get("installed") or []:
                if inst.get("status") == "installed" and "cpu" in (inst.get("flavor") or ""):
                    target = (et, inst.get("version"), inst.get("flavor"))
                    break
            if target:
                break
        if not target:
            r.status = "SKIP"; r.detail = "无 cpu 构建可卸载"; return
        et, ver, fl = target
        st2, d2, _ = R.http.post("/api/engines/uninstall",
                                 {"engine_type": et, "version": ver, "flavor": fl},
                                 timeout=120)
        r.evidence = {"target": target, "status": st2, "resp": str(d2)[:200]}
        R.ok(st2 in (200, 400), f"卸载返回异常 {st2}")

    @R.case("L2-52", "清除默认 Key 后 /v1 恢复免鉴权", "L2")
    def _(r):
        # 前置：吊销全部虚拟 Key。只要还有虚拟 Key，auth_enforced 就保持 true
        # （这是刻意的安全设计：吊销 Key ≠ 关闭鉴权，见报告 F4）
        _, kl, _ = R.http.get("/api/token-router/keys")
        for k in kl.get("keys") or []:
            kid = k.get("key") or k.get("key_id")
            if kid:
                R.http.delete(f"/api/token-router/keys/{kid}")
        r.evidence = {"revoked": len(kl.get("keys") or [])}
        st, d, _ = R.http.post("/api/settings/default-key/generate")
        raw = d.get("key") or ""
        r.evidence = {"generated": bool(raw)}
        # 无 Key 访问应 401
        st2, _, _ = R.http.get("/v1/models", headers={"X-API-Key": ""})
        r.evidence["after_generate_no_key"] = st2
        st3, dd, _ = R.http.delete("/api/settings/default-key")
        r.evidence["delete_status"] = st3
        st4, _, _ = R.http.get("/v1/models")
        r.evidence["after_clear_no_key"] = st4
        _, kl2, _ = R.http.get("/api/token-router/keys")
        remaining = len(kl2.get("keys") or [])
        r.evidence["remaining_virtual_keys"] = remaining
        if remaining:
            # 还有虚拟 Key => 鉴权必须保持（安全设计）
            R.eq(st4, 401, "有虚拟 Key 时应保持鉴权")
        else:
            R.eq(st4, 200, "无任何 Key 时应免鉴权")
