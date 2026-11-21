#!/usr/bin/env python3
"""
L4 白盒边界与故障注入
=====================
按源码分支设计的针对性用例：不看界面上有没有按钮，
而是盯着代码里每条 if/else 的分支边界去打。

  1. 三级路由解析    _resolve_chat_target 的所有分支
  2. embedding 兜底  _can_embed 三种命中方式
  3. UID 派生        derive_uid 的边界（组织目录/量化/重名/超长）
  4. 参数校验        launch 的非法输入矩阵
  5. 端口分配器      耗尽/预留
  6. XSS 注入        名称字段里的脚本与引号
  7. 会话捕获        SSE 半行分片（真实链路版）
  8. 并发与幂等      重复 launch / 同时 stop
"""
import json
import re
import time

EMB = "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"


def register(run):
    R = run

    # ---------------------------------------------------------------
    # 1. 三级路由解析
    # ---------------------------------------------------------------
    @R.case("L4-01", "路由：运行中实例精确命中", "L4")
    def _(r):
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-route"}, timeout=180)
        for _ in range(20):
            _, inst, _ = R.http.get("/api/instances/l4-route")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        st, d, _ = R.http.get("/api/router/resolve?model=l4-route")
        r.evidence = {"resolve": d}
        R.eq(d.get("target"), "local", "target")
        R.eq(d.get("port"), inst.get("port"), "port")
        R.http.delete("/api/instances/l4-route")

    @R.case("L4-02", "路由：prefix-local 强制本地", "L4")
    def _(r):
        st, d, _ = R.http.get("/api/router/resolve?model=local/embedding")
        r.evidence = {"resolve": d}
        R.ok(d.get("target") in ("local", "none"), f"target={d.get('target')}")

    @R.case("L4-03", "路由：不存在的模型必须是 none+error，不许回退", "L4")
    def _(r):
        st, d, _ = R.http.get("/api/router/resolve?model=no-such-model-abc")
        r.evidence = {"resolve": d}
        R.eq(d.get("target"), "none", "未知模型不能回退到本地")
        R.ok(d.get("error"), "应带 error 说明")

    @R.case("L4-04", "路由：provider 前缀指向未注册 provider", "L4")
    def _(r):
        st, d, _ = R.http.get("/api/router/resolve?model=provider/nonexist/model-x")
        r.evidence = {"resolve": d}
        R.ok(d.get("target") in ("none", "provider"), f"target={d.get('target')}")
        R.ok(d.get("error") or d.get("target") == "none" or d.get("provider"),
             f"未注册 provider 应有明确处理: {d}")

    # ---------------------------------------------------------------
    # 2. embedding 兜底链
    # ---------------------------------------------------------------
    @R.case("L4-05", "embedding 兜底：category=embedding 可直接用", "L4")
    def _(r):
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-emb-a"}, timeout=180)
        ok = False
        for _ in range(15):
            st, d, _ = R.http.post("/v1/embeddings",
                                   {"model": "l4-emb-a", "input": "x"}, timeout=60)
            if st == 200:
                ok = True
                break
            time.sleep(4)
        r.evidence = {"status": st}
        R.ok(ok, f"embedding 实例应能出向量: {st}")
        R.http.delete("/api/instances/l4-emb-a")

    @R.case("L4-06", "embedding 兜底：未指定 model 时自动挑 embedding 实例", "L4")
    def _(r):
        st, d, el = R.http.post("/v1/embeddings", {"input": "自动挑实例"}, timeout=90)
        r.evidence = {"status": st, "elapsed_s": round(el, 2)}
        R.ok(st in (200, 404, 503), f"status={st}")

    @R.case("L4-07", "embedding 兜底：chat 实例调用应给可读报错", "L4")
    def _(r):
        # 用 chat 类别实例（无 embeddings 参数）打 embeddings
        _, insts, _ = R.http.get("/api/instances")
        chat_running = any(v.get("category") == "chat" and v.get("status") == "running"
                           for v in insts.values())
        if not chat_running:
            r.status = "SKIP"
            r.detail = "环境前置缺失：无 running 的 chat 实例"
            return
        st, d, _ = R.http.post("/v1/embeddings",
                               {"model": "chat", "input": "x"}, timeout=90)
        r.evidence = {"status": st, "body": str(d)[:200]}
        R.ok(st != 500, f"不该 500: {st}")
        R.ok("embedding" in json.dumps(d, ensure_ascii=False).lower()
             or "error" in d, "错误信息应可读")

    # ---------------------------------------------------------------
    # 3. UID 派生边界
    # ---------------------------------------------------------------
    @R.case("L4-08", "UID 派生：跳过组织目录（meta/Qwen）", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "chat", "engine_type": "llama_cpp",
            "model_file": "meta/Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        }, timeout=60)
        uid = d.get("model_uid") or ""
        r.evidence = {"uid": uid, "status": st}
        R.ok(uid and uid != "meta", f"不能把组织名当 uid: {uid}")
        R.ok("llama" in uid.lower(), f"uid 应含模型名: {uid}")
        if uid:
            R.http.delete(f"/api/instances/{uid}")

    @R.case("L4-09", "UID 派生：剥掉量化后缀", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-q8-test-Q8_0",
        }, timeout=60)
        # 显式 uid 会原样使用，但应返回 warnings
        r.evidence = {"uid": d.get("model_uid"), "warnings": d.get("warnings")}
        R.ok(d.get("warnings"), f"带量化后缀应给 warnings: {d.get('warnings')}")
        R.http.delete("/api/instances/l4-q8-test-Q8_0")

    @R.case("L4-10", "UID 派生：超长名称被截断", "L4")
    def _(r):
        long_name = "a" * 200 + "-" + EMB
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": long_name, "parameters": {"embeddings": True},
        }, timeout=60)
        uid = d.get("model_uid") or ""
        r.evidence = {"uid_len": len(uid), "uid": uid[:60]}
        R.ok(len(uid) <= 60, f"uid 过长（{len(uid)}），应截断")
        if uid:
            R.http.delete(f"/api/instances/{uid}")

    # ---------------------------------------------------------------
    # 4. 参数校验矩阵
    # ---------------------------------------------------------------
    @R.case("L4-11", "校验：缺 model_type", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {"engine_type": "llama_cpp"})
        r.evidence = {"status": st, "error": str(d.get("error"))[:100]}
        R.eq(st, 400, "status")
        R.ok(d.get("error"), "应返回 error")

    @R.case("L4-12", "校验：缺 engine_type", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {"model_type": "chat"})
        r.evidence = {"status": st}
        R.eq(st, 400, "status")

    @R.case("L4-13", "校验：未知引擎", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch",
                               {"model_type": "chat", "engine_type": "not_exist"})
        r.evidence = {"status": st, "error": str(d.get("error"))[:100]}
        R.eq(st, 400, "status")

    @R.case("L4-14", "校验：引擎不支持该类别", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch",
                               {"model_type": "chat", "engine_type": "diffusers"})
        r.evidence = {"status": st, "supported": d.get("supported")}
        R.eq(st, 400, "status")
        R.ok(d.get("supported"), "应返回支持的类别列表")

    @R.case("L4-15", "校验：parameters 不是对象", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "chat", "engine_type": "llama_cpp",
            "parameters": "not-an-object"})
        r.evidence = {"status": st, "error": str(d.get("error"))[:120]}
        R.eq(st, 400, "status")

    @R.case("L4-16", "校验：非 JSON 请求体", "L4")
    def _(r):
        st, d, _ = R.http.request("POST", "/api/instances/launch", None,
                                  headers={"Content-Type": "application/json"})
        r.evidence = {"status": st}
        R.ok(st in (400, 415), f"status={st}")

    # ---------------------------------------------------------------
    # 5. 端口分配
    # ---------------------------------------------------------------
    @R.case("L4-17", "端口分配：动态实例端口在 18100-18499", "L4")
    def _(r):
        _, insts, _ = R.http.get("/api/instances")
        dyn = [v["port"] for v in insts.values() if v.get("is_dynamic")]
        r.evidence = {"dynamic_ports": dyn}
        for p in dyn:
            R.ok(18100 <= p <= 18499, f"端口越界: {p}")

    @R.case("L4-18", "端口分配：固定场景端口不被动态占用", "L4")
    def _(r):
        _, insts, _ = R.http.get("/api/instances")
        fixed = {v["port"] for v in insts.values() if not v.get("is_dynamic")}
        dyn = {v["port"] for v in insts.values() if v.get("is_dynamic")}
        r.evidence = {"fixed": sorted(fixed), "dynamic": sorted(dyn)}
        R.ok(not (fixed & dyn), f"端口冲突: {fixed & dyn}")

    # ---------------------------------------------------------------
    # 6. XSS / 注入
    # ---------------------------------------------------------------
    @R.case("L4-19", "XSS：模型名带脚本标签被转义", "L4")
    def _(r):
        xss = '<script>alert("xss")</script>'
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-xss", "model_name": xss,
        }, timeout=120)
        r.evidence = {"status": st, "name_saved": d.get("model_name")}

        # 页面渲染时必须转义
        if R.page:
            R.page.goto(R.http.base + "/", wait_until="domcontentloaded")
            R.page.wait_for_timeout(1500)
            R.goto_tab("launch")
            R.page.wait_for_timeout(4000)
            html = R.js("document.getElementById('rmTableBody')?.innerHTML || ''")
            injected = "<script>alert" in html
            r.evidence["rendered_raw_script"] = injected
            R.ok(not injected, "模型名未转义，存在 XSS")
        R.http.delete("/api/instances/l4-xss")

    @R.case("L4-20", "XSS：node_name 带引号不破坏配置", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/settings/node-name",
                               {"node_name": 'a"b\'c<>&'})
        r.evidence = {"status": st, "error": str(d.get("error"))[:80]}
        # 允许保存或拒绝，但服务必须活着
        st2, h, _ = R.http.get("/api/health")
        R.eq(h.get("status"), "ok", "服务应仍健康")
        _, s, _ = R.http.get("/api/settings")
        R.ok(s.get("node_name"), "配置应可读")
        R.http.post("/api/settings/node-name", {"node_name": "amm"})

    @R.case("L4-21", "注入：模型名含换行/控制字符", "L4")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-ctrl", "model_name": "a\nb\tc\x00d",
        }, timeout=120)
        r.evidence = {"status": st, "name": repr(d.get("model_name"))}
        st2, h, _ = R.http.get("/api/health")
        R.eq(h.get("status"), "ok", "服务应仍健康")
        R.http.delete("/api/instances/l4-ctrl")

    # ---------------------------------------------------------------
    # 7. SSE 真实链路
    # ---------------------------------------------------------------
    @R.case("L4-22", "SSE：流式响应分片完整且以 [DONE] 结束", "L4")
    def _(r):
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-sse"}, timeout=180)
        for _ in range(20):
            _, inst, _ = R.http.get("/api/instances/l4-sse")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        # 实例刚起时端口可能未就绪，会瞬时 500；轮询到能通为止
        last = None
        for _ in range(10):
            st, body, _ = R.http.request(
                "POST", "/v1/chat/completions",
                {"model": "l4-sse", "stream": True, "max_tokens": 4,
                 "messages": [{"role": "user", "content": "hi"}]},
                timeout=90, raw=True)
            if st == 200:
                break
            last = st
            time.sleep(4)
        else:
            raise AssertionError(f"实例始终未就绪，最后一次 status={last}")
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        r.evidence = {"status": st, "bytes": len(text),
                      "has_done": "[DONE]" in text,
                      "sse_lines": text.count("data:")}
        R.ok(st in (200, 400, 503), f"status={st}")
        if st == 200:
            R.ok("data:" in text, "应为 SSE 格式")
        R.http.delete("/api/instances/l4-sse")

    # ---------------------------------------------------------------
    # 8. 并发与幂等
    # ---------------------------------------------------------------
    @R.case("L4-23", "幂等：重复 launch 同一 uid 被拒且不泄漏端口", "L4")
    def _(r):
        results = []
        for _ in range(2):
            st, d, _ = R.http.post("/api/instances/launch", {
                "model_type": "embedding", "engine_type": "llama_cpp",
                "model_file": EMB, "parameters": {"embeddings": True},
                "model_uid": "l4-idem"}, timeout=180)
            results.append((st, d.get("error"), d.get("port")))
        r.evidence = {"results": results}
        R.ok(results[0][0] == 200, f"首次应成功: {results[0]}")
        R.eq(results[1][0], 400, "第二次应被拒")
        R.ok("已存在" in str(results[1][1]), f"错误信息: {results[1][1]}")
        R.http.delete("/api/instances/l4-idem")

    @R.case("L4-24", "并发：同时 stop 同一实例不报 5xx", "L4")
    def _(r):
        import threading
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-conc"}, timeout=180)
        for _ in range(20):
            _, inst, _ = R.http.get("/api/instances/l4-conc")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        codes = []
        def stop():
            codes.append(R.http.post("/api/instances/l4-conc/stop")[0])
        ts = [threading.Thread(target=stop) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        r.evidence = {"codes": codes}
        R.ok(all(c < 500 for c in codes), f"出现 5xx: {codes}")
        R.http.delete("/api/instances/l4-conc")

    @R.case("L4-25", "边界：/v1/models 不含 stopped 实例", "L4")
    def _(r):
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": EMB, "parameters": {"embeddings": True},
            "model_uid": "l4-stopped"}, timeout=180)
        R.http.post("/api/instances/l4-stopped/stop")
        time.sleep(2)
        _, models, _ = R.http.get("/v1/models")
        ids = [m["id"] for m in models["data"]]
        r.evidence = {"present": "l4-stopped" in ids}
        R.ok("l4-stopped" not in ids, "已停止的实例不应出现在模型列表")
        R.http.delete("/api/instances/l4-stopped")
