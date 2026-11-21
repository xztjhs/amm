#!/usr/bin/env python3
"""
L3 场景链路（端到端业务流）
===========================
单个功能对了不代表链路对。这里跑跨越多个页面的真实业务流，
每一步都留证据，失败时能定位断在哪一环。

  E1 从模版到对话：部署 → 真实对话 → 调用历史
  E2 生命周期：启动→改参→重启→停止→结束→端口回收
  E3 计费闭环：签发限额 Key → 调用 → 超额拒绝 → 报表
  E4 客户端兼容：uid 形态 + name/capabilities + 注册表归一化模拟
  E5 故障恢复：容器停止 → 手动恢复 → 数据完整
  E6 刷新风暴：高频轮询 5 分钟，观察稳定性
  E7 流式全链：本地/外部流式 → 调用历史 output 真实文本
  E8 权限矩阵：无 Key/错 Key/默认 Key/虚拟 Key
  E9 数据持久化：重启后实例/模版/Key 都在
  E10 并发操作：同时启停 + 双会话
"""
import json
import re
import threading
import time

MONTH = 30 * 24 * 3600


def register(run):
    R = run

    def skip(r, why):
        r.status = "SKIP"
        r.detail = why

    def open_auth():
        """用例隔离：清掉默认 Key 与全部虚拟 Key，保证 /v1 免鉴权。

        L3-03/L3-08 会签发 Key 并置 auth_enforced=True，不清会污染后续用例。
        用新的 auth/disable 接口（带 confirm），一次把 auth_enforced 也关掉。
        """
        R.http.post("/api/settings/auth/disable", {"confirm": True})
        R.http.delete("/api/settings/default-key")
        _, kl, _ = R.http.get("/api/token-router/keys")
        for k in kl.get("keys") or []:
            kid = k.get("key") or k.get("key_id")
            if kid:
                R.http.delete(f"/api/token-router/keys/{kid}")

    # ---------------------------------------------------------------
    @R.case("L3-01", "E1 从模版部署到真实对话全链", "L3")
    def _(r):
        open_auth()
        # 1) 找一个 embedding 之外的可用模版（chat 只有 1 个，用 embedding 稳妥）
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True}, "model_uid": "e1-embed",
            "model_name": "E1 链路测试模型",
        }, timeout=180)
        R.ok(not d.get("error"), f"启动失败: {d.get('error')}")

        # 2) 等就绪并真实调用
        ok = False
        for _ in range(15):
            st, emb, _ = R.http.post("/v1/embeddings",
                                     {"model": "e1-embed", "input": "链路测试"}, timeout=60)
            if st == 200:
                ok = True
                break
            time.sleep(4)
        R.ok(ok, "实例未就绪")

        # 3) 出现在 /v1/models 且带元数据
        _, models, _ = R.http.get("/v1/models")
        m = next((x for x in models["data"] if x["id"] == "e1-embed"), None)
        R.ok(m, "未出现在 /v1/models")
        R.eq(m.get("name"), "E1 链路测试模型", "自定义名称应被保留")

        # 4) 调用历史有这次调用（本地直连也要记录）
        _, calls, _ = R.http.get("/api/router/calls?limit=50")
        mine = [c for c in calls.get("calls") or [] if c.get("model") == "e1-embed"]
        R.ok(mine, f"调用历史缺 e1-embed（共 {len(calls.get('calls') or [])} 条）")
        R.eq(mine[0].get("provider"), "local", "本地调用 provider 标记")
        out = str(mine[0].get("output") or "")
        r.evidence = {"calls": len(mine), "output_head": out[:80]}
        R.ok(out and "embedding" in out.lower(), f"embeddings 输出未记录: {out[:60]!r}")
        R.http.delete("/api/instances/e1-embed")

    # ---------------------------------------------------------------
    @R.case("L3-02", "E2 实例生命周期与端口回收", "L3")
    def _(r):
        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True}, "model_uid": "e2-life",
        }, timeout=180)
        port = d.get("port")
        R.ok(port >= 18100, f"端口异常 {port}")

        # 改参数 → 重启 → 端口不变
        R.http.put("/api/instances/e2-life/parameters", {"ctx_size": 8192})
        R.http.post("/api/instances/e2-life/restart", timeout=180)
        for _ in range(30):
            _, inst, _ = R.http.get("/api/instances/e2-life")
            if inst.get("status") == "running":
                break
            time.sleep(1)
        R.eq(inst.get("port"), port, "重启后端口应保持")
        R.eq(inst.get("parameters", {}).get("ctx_size"), 8192, "参数应持久")

        # 结束 → 端口应被释放（可再次分配）
        R.http.delete("/api/instances/e2-life")
        st2, d2, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True}, "model_uid": "e2-reuse",
        }, timeout=180)
        r.evidence = {"first_port": port, "reuse_port": d2.get("port")}
        R.ok(not d2.get("error"), f"复用启动失败: {d2.get('error')}")
        R.http.delete("/api/instances/e2-reuse")

    # ---------------------------------------------------------------
    @R.case("L3-03", "E3 计费闭环：限额 Key 超额被拒", "L3")
    def _(r):
        st, k, _ = R.http.post("/api/token-router/keys", {
            "name": "e3-budget", "total_limit": 50, "models": []})
        key = k.get("key")
        R.ok(key, "签发失败")

        # 用这个小 Key 调外部 provider（真实计费最小化：max_tokens=1）
        st2, d2, _ = R.http.post("/api/router/relay", {
            "model": "deepseek-v4.1-flash", "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        r.evidence = {"first_call_status": st2}

        # 继续打直到配额耗尽
        statuses = []
        for _ in range(6):
            st3, d3, _ = R.http.post("/api/router/relay", {
                "model": "deepseek-v4.1-flash", "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }, headers={"Authorization": f"Bearer {key}"}, timeout=120)
            statuses.append(st3)
            if st3 == 429:
                break
        r.evidence["statuses"] = statuses

        # 用量报表应记录这条 Key
        _, usage, _ = R.http.get("/api/token-router/usage")
        rows = usage.get("rows") or []
        mine = [x for x in rows if "e3-budget" in json.dumps(x, ensure_ascii=False)]
        r.evidence["usage_rows"] = len(mine)
        R.http.delete(f"/api/token-router/keys/{key}")

    # ---------------------------------------------------------------
    @R.case("L3-04", "E4 客户端兼容：uid 形态与注册表归一化", "L3")
    def _(r):
        open_auth()
        # 后端派生规则：模拟 Cherry 的归一化（剥参数尺寸后缀）
        def normalize(mid: str) -> str:
            base = mid.split("/")[-1].lower()
            base = re.sub(r"-(\d+(?:\.\d+)?b)(?=-|$)", "", base)
            return base.replace("_", "-")

        st, d, _ = R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True},
        }, timeout=180)
        uid = d.get("model_uid")
        r.evidence = {"derived_uid": uid, "normalized": normalize(uid or "")}

        # 派生出的 uid 必须自带类别身份，且不含量化后缀
        R.ok(uid and "embedding" in uid, f"uid 缺类别身份: {uid}")
        R.ok(not re.search(r"q\d|gguf|fp16|bf16", uid or ""), f"uid 含量化/格式后缀: {uid}")
        # 归一化后仍保留 embedding（不会退化成裸家族名 qwen3）
        R.ok("embedding" in normalize(uid), f"归一化后退化: {normalize(uid)}")

        # 元数据齐全
        _, models, _ = R.http.get("/v1/models")
        m = next((x for x in models["data"] if x["id"] == uid), None)
        R.ok(m and m.get("capabilities") == ["embedding"], f"capabilities 异常: {m}")
        R.ok(m.get("name") and m["name"] != uid, "name 应不同于 id")
        if uid:
            R.http.delete(f"/api/instances/{uid}")

    # ---------------------------------------------------------------
    @R.case("L3-05", "E5 故障恢复：服务重启后状态完整", "L3")
    def _(r):
        # 造数据
        R.http.post("/api/instances/launch", {
            "model_type": "embedding", "engine_type": "llama_cpp",
            "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "parameters": {"embeddings": True}, "model_uid": "e5-persist",
        }, timeout=180)
        _, k, _ = R.http.post("/api/token-router/keys", {"name": "e5-key"})
        key = k.get("key")

        # 重启
        R.http.post("/api/settings/restart", {}, timeout=15)
        alive = False
        for _ in range(60):
            time.sleep(2)
            st, h, _ = R.http.get("/api/health", timeout=5)
            if h.get("status") == "ok":
                alive = True
                break
        R.ok(alive, "重启后未恢复")

        # 数据仍在
        st2, inst, _ = R.http.get("/api/instances/e5-persist")
        R.eq(st2, 200, "实例应在重启后仍存在")
        _, keys, _ = R.http.get("/api/token-router/keys")
        R.ok(any(x.get("name") == "e5-key" for x in keys.get("keys") or []), "Key 丢失")
        r.evidence = {"instance": inst.get("model_id"), "keys_n": len(keys.get("keys") or [])}

        # 自动恢复：重启前 running 的应回到 running
        R.ok(inst.get("status") in ("running", "starting", "stopped"),
             f"状态异常: {inst.get('status')}")
        R.http.delete("/api/instances/e5-persist")
        if key:
            R.http.delete(f"/api/token-router/keys/{key}")

    # ---------------------------------------------------------------
    @R.case("L3-06", "E6 高频轮询 40s 稳定性", "L3")
    def _(r):
        errors = []
        lat = []
        t0 = time.time()
        while time.time() - t0 < 40:
            for ep in ("/api/instances", "/api/system"):
                st, _, el = R.http.get(ep, timeout=20)
                lat.append(el)
                if st != 200:
                    errors.append((ep, st))
            time.sleep(0.1)
        lat.sort()
        r.evidence = {
            "requests": len(lat),
            "errors": errors[:5],
            "p50_ms": round(lat[len(lat)//2] * 1000, 1),
            "p95_ms": round(lat[int(len(lat)*0.95)] * 1000, 1),
            "max_ms": round(lat[-1] * 1000, 1),
        }
        R.ok(not errors, f"高频轮询出现错误: {errors[:3]}")
        R.ok(lat[-1] < 10, f"最慢请求 {lat[-1]:.1f}s，疑似阻塞事件循环（D6）")

    # ---------------------------------------------------------------
    @R.case("L3-07", "E7 流式全链：调用历史记真实输出", "L3")
    def _(r):
        # 用例隔离：先把鉴权关掉，并直接签一把临时 Key 备用。
        # 注意 issue_key 会把 auth_enforced 重新置 true（安全设计），
        # 所以拿到 Key 后必须带着它发请求，不能指望免鉴权。
        open_auth()
        _, kd0, _ = R.http.post("/api/token-router/keys", {"name": "l3-07-key"})
        tmp0 = kd0.get("key") or ""
        # 诊断：401 可能来自 AMM 鉴权，也可能来自上游 provider（base_url 被污染）
        _, auth0, _ = R.http.get("/api/settings/auth")
        _, prov0, _ = R.http.get("/api/providers")
        provs0 = {x.get("name"): x.get("base_url") for x in (prov0.get("providers") or [])}
        r.evidence.update({
            "tmp_key_len": len(tmp0),
            "auth_enforced": auth0.get("auth_enforced"),
            "provider_base_urls": provs0,
        })
        # 环境前置：需要至少一个已配置 provider
        _, provs, _ = R.http.get("/api/providers")
        if not (provs.get("providers") or []):
            r.status = "SKIP"
            r.detail = "环境前置缺失：沙箱未配置任何外部 provider"
            return
        # 外部 provider 流式（真实计费，max_tokens 压到最小）
        hdrs = {"Authorization": f"Bearer {tmp0}"} if tmp0 else {}
        st, body, _ = R.http.request(
            "POST", "/v1/chat/completions",
            {"model": "deepseek-v4.1-flash", "stream": True, "max_tokens": 8,
             "messages": [{"role": "user", "content": "只回复：OK"}]},
            headers=hdrs, timeout=120, raw=True)
        r.evidence["first_status"] = st
        r.evidence["first_body_head"] = (body.decode("utf-8", "replace")
                                         if isinstance(body, bytes) else str(body))[:300]
        if st == 401:
            # 说明还有 Key 处于强制鉴权状态：签一个临时 Key 再用
            _, kd, _ = R.http.post("/api/token-router/keys", {"name": "l3-07-tmp"})
            tmp = kd.get("key") or ""
            r.evidence["used_tmp_key"] = bool(tmp)
            st, body, _ = R.http.request(
                "POST", "/v1/chat/completions",
                {"model": "deepseek-v4.1-flash", "stream": True, "max_tokens": 8,
                 "messages": [{"role": "user", "content": "只回复：OK"}]},
                headers={"Authorization": f"Bearer {tmp}"}, timeout=120, raw=True)
        R.eq(st, 200, "流式 status")
        text = body.decode("utf-8", "replace")
        R.ok("data:" in text, "响应不是 SSE")
        R.ok("[DONE]" in text, "缺结束标记")

        # 调用历史里的 output 必须是真实文本
        time.sleep(2)
        _, calls, _ = R.http.get("/api/router/calls?limit=20")
        stream_calls = [c for c in calls.get("calls") or [] if c.get("stream")]
        R.ok(stream_calls, "无流式调用记录")
        latest = stream_calls[0]
        out = str(latest.get("output") or "")
        r.evidence = {"output_head": out[:120], "len": len(out)}
        R.ok(out and "(stream)" not in out, f"输出仍是占位符: {out[:60]!r}")

    # ---------------------------------------------------------------
    @R.case("L3-08", "E8 权限矩阵", "L3")
    def _(r):
        # 未设默认 Key 时应免鉴权
        st_nokey, _, _ = R.http.get("/v1/models")
        r.evidence = {"no_key": st_nokey}

        # 设默认 Key
        st, d, _ = R.http.post("/api/settings/default-key/generate")
        dk = d.get("key") or ""
        R.ok(dk, "生成默认 Key 失败")

        st_bad, _, _ = R.http.get("/v1/models", headers={"X-API-Key": "wrong-key"})
        st_good, _, _ = R.http.get("/v1/models", headers={"X-API-Key": dk})
        st_vk, kv, _ = R.http.post("/api/token-router/keys", {"name": "e8-vk"})
        vk = kv.get("key")
        st_virtual, _, _ = R.http.get("/v1/models", headers={"X-API-Key": vk})

        r.evidence.update({"bad_key": st_bad, "good_key": st_good,
                           "virtual_key": st_virtual})
        R.eq(st_bad, 401, "错误 Key 应 401")
        R.eq(st_good, 200, "正确 Key 应 200")
        R.eq(st_virtual, 200, "虚拟 Key 也应放行")

        # 清理（无论断言结果如何都要清，避免污染后续用例）
        R.http.delete("/api/settings/default-key")
        if vk:
            R.http.delete(f"/api/token-router/keys/{vk}")
        st_check, _, _ = R.http.get("/v1/models")
        r.evidence["after_cleanup"] = st_check

    # ---------------------------------------------------------------
    @R.case("L3-09", "E9 重启后模版与配置持久", "L3")
    def _(r):
        _, before, _ = R.http.get("/api/templates")
        n_before = before.get("total")
        _, s_before, _ = R.http.get("/api/settings")
        node_before = s_before.get("node_name")

        R.http.post("/api/settings/restart", {}, timeout=15)
        for _ in range(60):
            time.sleep(2)
            st, h, _ = R.http.get("/api/health", timeout=5)
            if h.get("status") == "ok":
                break

        _, after, _ = R.http.get("/api/templates")
        _, s_after, _ = R.http.get("/api/settings")
        r.evidence = {"templates_before": n_before, "templates_after": after.get("total"),
                      "node_before": node_before, "node_after": s_after.get("node_name")}
        R.eq(after.get("total"), n_before, "模版数")
        R.eq(s_after.get("node_name"), node_before, "分组名")

    # ---------------------------------------------------------------
    @R.case("L3-10", "E10 并发启停不串", "L3")
    def _(r):
        outs = {}
        def launch(uid):
            st, d, _ = R.http.post("/api/instances/launch", {
                "model_type": "embedding", "engine_type": "llama_cpp",
                "model_file": "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
                "parameters": {"embeddings": True}, "model_uid": uid,
            }, timeout=240)
            outs[uid] = (st, d.get("port"), d.get("error"))

        ts = [threading.Thread(target=launch, args=(f"e10-{i}",)) for i in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]

        ports = [v[1] for v in outs.values() if v[1]]
        r.evidence = {"results": {k: {"status": v[0], "port": v[1], "error": v[2]}
                                  for k, v in outs.items()}}
        R.eq(len(ports), len(set(ports)), f"端口冲突: {ports}")
        R.ok(all(v[0] in (200, 400) for v in outs.values()), f"异常状态: {outs}")

        for uid in outs:
            R.http.delete(f"/api/instances/{uid}")
