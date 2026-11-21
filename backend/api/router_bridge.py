"""
Router Bridge — 外部 provider 汇聚 + Token Router API（R3/R4）
=============================================================
  GET/POST/DELETE /api/providers[/{name}]       provider 注册管理
  GET/POST         /api/token-router/keys       虚拟 key 签发/列表
  DELETE           /api/token-router/keys/{key} 吊销 key
  GET              /api/token-router/usage      用量报表
  GET              /api/router/resolve?model=   路由决策预览（调试用）
  POST             /api/router/relay            统一转发（OpenAI 兼容透传）
"""
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

import aiohttp
from aiohttp import web

from backend.core.router import RouterStore, TokenRouter
from backend.core.sse_capture import SSETextCapture

logger = logging.getLogger("AMM.RouterBridge")

# provider 类型 -> 默认 base_url
_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "icompify": "https://api.icompify.com/v1",
}


def _latency_metrics(e2e_ms: float, out_tokens: int,
                     ttft_ms: float = None) -> dict:
    """由总耗时与输出 token 数推导常见推理指标（毫秒 / tokens每秒）。

    e2e   : 端到端总耗时
    ttft  : 首 token 延迟（流式才有真值，非流式按 0 计）
    itl   : 相邻 token 间隔 = (e2e - ttft) / (out_tokens - 1)
    tpot  : 每输出 token 平均耗时 = (e2e - ttft) / out_tokens
    tps   : 输出吞吐 = out_tokens / e2e(秒)
    """
    e2e_ms = max(e2e_ms, 0.001)
    ttft_ms = float(ttft_ms or 0.0)
    gen_ms = max(e2e_ms - ttft_ms, 0.001)
    out_tokens = max(int(out_tokens or 0), 1)
    return {
        "e2e_ms": round(e2e_ms, 2),
        "ttft_ms": round(ttft_ms, 2),
        "itl_ms": round(gen_ms / max(out_tokens - 1, 1), 2),
        "tpot_ms": round(gen_ms / out_tokens, 2),
        "tps": round(out_tokens / (e2e_ms / 1000.0), 2),
    }


def _summarize_messages(body: dict, limit: int = 1200) -> str:
    """把 messages 压成可读的输入摘要（保留角色与内容前若干字）"""
    parts = []
    for m in (body.get("messages") or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role", "?")
        content = m.get("content")
        if isinstance(content, list):
            txt = " ".join(str(c.get("text", "")) for c in content
                           if isinstance(c, dict) and c.get("type") == "text")
        else:
            txt = str(content or "")
        parts.append(f"[{role}] {txt[:300]}")
    s = "\n".join(parts)
    return s[:limit]


def _summarize_output(data) -> str:
    """从响应里取出模型输出的文本"""
    try:
        if isinstance(data, dict):
            # 流式：调用方已经把累积文本放在 text 里
            if data.get("stream") and "text" in data:
                text = str(data.get("text") or "").strip()
                if not text:
                    return "(stream) 未解析到文本" if not data.get("events") else "(stream) 输出为空"
                return text[:1500]
            if data.get("choices"):
                msg = data["choices"][0].get("message") or {}
                # 不同 provider 的字段名不统一：content / reasoning_content / reasoning
                for key in ("content", "reasoning_content", "reasoning", "text"):
                    v = msg.get(key)
                    if v:
                        return str(v)[:1500]
                return ""
    except Exception:
        pass
    return ""


def _record_call(router, key, key_cfg, model, provider, body, data, metrics, status="ok"):
    """记录一次调用的输入输出详情，供 Log Center 与「调用历史」查询。"""
    try:
        kid = (key_cfg or {}).get("key_id") or (key[-8:] if key else "anonymous")
        router.record_call({
            "ts": time.time(),
            "key": kid,
            "model": model,
            "provider": provider or "",
            "status": status,
            "stream": bool(body.get("stream")),
            "input": _summarize_messages(body) if isinstance(body, dict) else "",
            "output": _summarize_output(data),
            "params": {
                "temperature": body.get("temperature"),
                "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
                "top_p": body.get("top_p"),
            } if isinstance(body, dict) else {},
            "metrics": metrics or {},
        })
    except Exception as e:
        logger.warning(f"记录调用历史失败: {e}")


def setup_routes(app: web.Application, manager):
    amm_root = Path(os.environ.get("AMM_ROOT", "/amm"))
    store = RouterStore(str(amm_root / "data" / "router.json"))
    router = TokenRouter(manager, store)
    app["token_router"] = router

    class Handlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        # ---------------- providers ----------------
        async def list_providers(self, req):
            return self._json({"providers": router.list_providers(reveal=True),
                               "known_types": list(_DEFAULT_BASE.keys())})

        async def get_provider(self, req):
            name = req.match_info["name"]
            for p in router.list_providers(reveal=True):
                if p["name"] == name:
                    return self._json(p)
            return self._json({"error": f"provider 不存在: {name}"}, 404)

        async def upsert_provider(self, req):
            name = req.match_info["name"]
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "请求体必须是对象"}, 400)
            if not body.get("base_url") and body.get("type") in _DEFAULT_BASE:
                body["base_url"] = _DEFAULT_BASE[body["type"]]
            try:
                result = router.upsert_provider(name, body)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(result)

        async def fetch_models(self, req):
            """用 provider 的 url + key 拉取全部可用模型"""
            name = req.match_info["name"]
            return self._json(await router.fetch_provider_models(name))

        async def upsert_provider_body(self, req):
            """POST /api/providers（不带 name，body 里含 name）—— 供前端新建/编辑"""
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            name = (body.pop("name", "") or "").strip()
            if not name:
                return self._json({"error": "缺少 provider 名称"}, 400)
            if not body.get("base_url") and body.get("type") in _DEFAULT_BASE:
                body["base_url"] = _DEFAULT_BASE[body["type"]]
            try:
                result = router.upsert_provider(name, body)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(result)

        async def delete_provider(self, req):
            name = req.match_info["name"]
            return self._json({"success": router.delete_provider(name)})

        async def test_provider(self, req):
            """探活 provider：请求 /models"""
            name = req.match_info["name"]
            cfg = (store.data.get("providers") or {}).get(name)
            if not cfg:
                return self._json({"error": f"provider 不存在: {name}"}, 404)
            key = router.resolve_provider_key(name)
            if not key:
                return self._json({"ok": False, "error": "未配置 API key（api_key 或 api_key_env）"}, 400)
            url = cfg.get("base_url", "").rstrip("/") + "/models"
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url, headers={"Authorization": f"Bearer {key}"},
                                     timeout=aiohttp.ClientTimeout(total=10)) as r:
                        body = await r.text()
                        return self._json({
                            "ok": r.status == 200,
                            "status": r.status,
                            "preview": body[:500],
                        })
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 502)

        # ---------------- token router keys ----------------
        async def list_keys(self, req):
            return self._json({"keys": router.list_keys(),
                               "note": "key 仅显示掩码；签发时返回明文一次"})

        async def issue_key(self, req):
            try:
                body = await req.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            return self._json(router.issue_key(body))

        async def update_key(self, req):
            key_id = req.match_info["key"]
            try:
                body = await req.json()
            except Exception:
                body = {}
            return self._json(router.update_key(key_id, body if isinstance(body, dict) else {}))

        async def reveal_key(self, req):
            """返回一次明文（用于「再次复制」）。仅本地同源调用。"""
            key_id = req.match_info["key"]
            for k, cfg in (store.data.get("keys") or {}).items():
                if (cfg.get("key_id") or k[-8:]) == key_id or k.endswith(key_id):
                    return self._json({"key_id": key_id, "key": k})
            return self._json({"error": f"key 不存在: {key_id}"}, 404)

        async def billing_export(self, req):
            """账单/详单导出（CSV）"""
            fmt = (req.query.get("format") or "detail").lower()
            rows, headers, filename = router.export_billing(fmt)
            import csv
            import io as _io
            buf = _io.StringIO()
            w = csv.writer(buf)
            w.writerow(headers)
            for r in rows:
                w.writerow([r.get(h, "") for h in headers])
            return web.Response(
                text="\ufeff" + buf.getvalue(),  # BOM 让 Excel 正确识别 UTF-8
                headers={"Content-Type": "text/csv; charset=utf-8",
                         "Content-Disposition": f'attachment; filename="{filename}"',
                         "Access-Control-Allow-Origin": "*"})

        async def revoke_key(self, req):
            key = req.match_info["key"]
            return self._json({"success": router.revoke_key(key)})

        async def usage(self, req):
            return self._json(router.usage_summary())

        # ---------------- routing ----------------
        async def resolve(self, req):
            model = (req.query.get("model") or "").strip()
            if not model:
                return self._json({"error": "缺少 model 参数"}, 400)
            return self._json(router.resolve(model))

        async def models_merged(self, req):
            return self._json({"object": "list", "data": router.all_models()})

        async def relay(self, req):
            """统一转发：把请求按路由结果透传到本地实例或外部 provider

            body: {model, messages, stream, ...} (OpenAI chat/completions 语义)
            """
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            model = (body.get("model") or "").strip()
            if not model:
                return self._json({"error": {"message": "missing model"}}, 400)
            t_start = time.perf_counter()

            # 鉴权 + 配额
            auth = req.headers.get("Authorization", "")
            key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            ok, reason, key_cfg = router.authenticate(key)
            if not ok:
                return self._json({"error": {"message": reason, "type": "auth_error"}}, 401)
            allowed, why = router.check_quota(key_cfg, model)
            if not allowed:
                return self._json({"error": {"message": why, "type": "quota_error"}}, 429)

            decision = router.resolve(model)
            target = decision.get("target")

            if target == "local":
                url = f"http://127.0.0.1:{decision['port']}/v1/chat/completions"
                headers = {"Content-Type": "application/json"}
                payload = dict(body)
                payload["model"] = decision["model"]
                provider_name = "local"
            elif target == "provider":
                pname = decision["provider"]
                cfg = (store.data.get("providers") or {}).get(pname) or {}
                pkey = router.resolve_provider_key(pname)
                if not pkey:
                    return self._json({"error": {"message": f"provider {pname} 未配置 key"}}, 502)
                url = cfg.get("base_url", "").rstrip("/") + "/chat/completions"
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Bearer {pkey}"}
                payload = dict(body)
                payload["model"] = decision["model"]
                provider_name = pname
            else:
                return self._json({"error": {"message": decision.get("error", "no route")}}, 404)

            stream = bool(payload.get("stream"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=payload, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=600)) as r:
                        if stream and r.status == 200:
                            resp = web.StreamResponse(status=200, headers={
                                "Content-Type": r.headers.get("Content-Type", "text/event-stream"),
                                "Cache-Control": "no-cache",
                                "Access-Control-Allow-Origin": "*",
                            })
                            await resp.prepare(req)
                            first_chunk_at = None
                            emitted = 0
                            cap = SSETextCapture()
                            async for chunk in r.content.iter_any():
                                if first_chunk_at is None:
                                    first_chunk_at = time.perf_counter()
                                cap.feed(chunk)
                                await resp.write(chunk)
                                emitted += len(chunk)
                            await resp.write_eof()
                            cap.close()
                            # 流式场景没有 usage，按字符数粗估 token
                            e2e_ms = (time.perf_counter() - t_start) * 1000
                            ttft_ms = ((first_chunk_at or t_start) - t_start) * 1000
                            est_out = max(1, emitted // 4)
                            _m = _latency_metrics(e2e_ms, est_out, ttft_ms=ttft_ms)
                            router.record_usage(
                                key, model, provider_name,
                                max(1, len(str(body.get("messages") or "")) // 4),
                                est_out,
                                key_cfg,
                                metrics=_m,
                            )
                            _record_call(router, key, key_cfg, model,
                                         provider_name, body,
                                         {"stream": True, "bytes": emitted,
                                          "text": cap.text(),
                                          "truncated": cap.truncated,
                                          "events": cap.events},
                                         _m, status="ok")
                            return resp

                        text = await r.text()
                        if r.status == 200:
                            try:
                                data = await r.json()
                            except Exception:
                                return self._json({"error": {"message": text[:500]}}, 502)
                            u = data.get("usage") or {}
                            e2e_ms = (time.perf_counter() - t_start) * 1000
                            out_tok = int(u.get("completion_tokens") or 0)
                            _m = _latency_metrics(e2e_ms, out_tok)
                            router.record_usage(
                                key, model, provider_name,
                                int(u.get("prompt_tokens") or 0),
                                out_tok,
                                key_cfg,
                                metrics=_m,
                            )
                            _record_call(router, key, key_cfg, model,
                                         provider_name, body, data, _m,
                                         status="ok")
                            return self._json(data)
                        return self._json({"error": {"message": text[:1000],
                                                     "type": "upstream_error",
                                                     "provider": provider_name}},
                                          r.status)
            except Exception as e:
                logger.error(f"relay 失败 model={model}: {e}")
                return self._json({"error": {"message": str(e), "type": "relay_error"}}, 502)

        async def get_default_key(self, req):
            """GET /api/settings/default-key —— 是否已配置（掩码）"""
            return self._json(router.get_default_key(reveal=False))

        async def gen_default_key(self, req):
            """POST /api/settings/default-key/generate —— 随机生成并返回明文"""
            return self._json(router.generate_default_key())

        async def reveal_default_key(self, req):
            """POST /api/settings/default-key/reveal —— 再次复制"""
            d = router.get_default_key(reveal=True)
            if not d.get("key"):
                return self._json({"error": "尚未配置默认 Key"}, 404)
            return self._json(d)

        async def clear_default_key(self, req):
            """DELETE /api/settings/default-key —— 清除后客户端可免 key 直连"""
            return self._json(router.clear_default_key())

        async def disable_auth(self, req):
            """POST /api/settings/auth/disable —— 彻底关闭鉴权（#2）

            危险操作：关闭后任何能访问本端口的人都能调 /v1/*。
            前端必须二次确认后才调用。
            """
            try:
                body = await req.json()
            except Exception:
                body = {}
            if not (body or {}).get("confirm"):
                return self._json({
                    "error": "需要显式确认：请在请求体传 {\"confirm\": true}",
                    "warning": "关闭鉴权后任何人都可调用 /v1/*",
                }, 400)
            return self._json(router.disable_auth_entirely())

        async def enable_auth(self, req):
            """POST /api/settings/auth/enable —— 重新开启鉴权（#2）"""
            return self._json(router.enable_auth())

        async def auth_status(self, req):
            """GET /api/settings/auth —— 当前鉴权状态（#2）"""
            d = router.get_default_key(reveal=False)
            keys = router.store.data.get("keys") or {}
            return self._json({
                "auth_enforced": bool(router.store.data.get("auth_enforced")),
                "has_default_key": d.get("has_key"),
                "default_key_masked": d.get("masked"),
                "virtual_key_count": len(keys),
            })

        async def update_model_meta(self, req):
            """PUT /api/router/models/{model_id} —— 编辑类型/状态/单价/上下文"""
            mid = req.match_info["model_id"]
            try:
                body = await req.json()
            except Exception:
                body = {}
            return self._json(router.update_model_meta(mid, body if isinstance(body, dict) else {}))

        async def list_calls(self, req):
            """GET /api/router/calls —— 调用历史（含输入输出与性能）"""
            try:
                since = float(req.query.get("since") or 0)
                until = float(req.query.get("until") or 0)
            except ValueError:
                since = until = 0
            return self._json(router.list_calls(
                model=req.query.get("model", ""),
                source=req.query.get("source", ""),
                level=req.query.get("status", ""),
                since=since, until=until,
                keyword=req.query.get("q", ""),
                limit=int(req.query.get("limit") or 200)))

        async def router_status(self, req):
            return self._json({
                "providers": len(router.list_providers()),
                "keys": len(router.list_keys()),
                "local_running": len(router.local_models()),
                "merged_models": len(router.all_models()),
                "usage": router.usage_summary()["totals"],
            })

    h = Handlers()
    app.router.add_get("/api/providers", h.list_providers)
    app.router.add_post("/api/providers", h.upsert_provider_body)
    app.router.add_post("/api/providers/{name}/models", h.fetch_models)
    app.router.add_post("/api/providers/{name}", h.upsert_provider)
    app.router.add_get("/api/providers/{name}", h.get_provider)
    app.router.add_delete("/api/providers/{name}", h.delete_provider)
    app.router.add_post("/api/providers/{name}/test", h.test_provider)

    app.router.add_get("/api/token-router/keys", h.list_keys)
    app.router.add_post("/api/token-router/keys", h.issue_key)
    app.router.add_delete("/api/token-router/keys/{key}", h.revoke_key)
    app.router.add_put("/api/token-router/keys/{key}", h.update_key)
    app.router.add_post("/api/token-router/keys/{key}/reveal", h.reveal_key)
    app.router.add_get("/api/token-router/billing/export", h.billing_export)
    app.router.add_get("/api/token-router/usage", h.usage)

    app.router.add_get("/api/router/resolve", h.resolve)
    app.router.add_get("/api/router/status", h.router_status)
    app.router.add_get("/api/settings/default-key", h.get_default_key)
    app.router.add_post("/api/settings/default-key/generate", h.gen_default_key)
    app.router.add_post("/api/settings/default-key/reveal", h.reveal_default_key)
    app.router.add_delete("/api/settings/default-key", h.clear_default_key)
    app.router.add_get("/api/settings/auth", h.auth_status)
    app.router.add_post("/api/settings/auth/disable", h.disable_auth)
    app.router.add_post("/api/settings/auth/enable", h.enable_auth)
    app.router.add_get("/api/router/models", h.models_merged)
    app.router.add_put("/api/router/models/{model_id}", h.update_model_meta)
    app.router.add_get("/api/router/calls", h.list_calls)
    app.router.add_post("/api/router/relay", h.relay)
