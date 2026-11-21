"""
Template Bridge — 模版 API（R2 launch-model）
=============================================
  GET    /api/templates?category=&q=          模版列表
  GET    /api/templates/{template_id}         模版详情
  POST   /api/templates                       保存模版（写 custom/）
  PUT    /api/templates/{template_id}         编辑模版（内置模版 -> 另存为副本）
  POST   /api/templates/{template_id}/duplicate  复制模版
  DELETE /api/templates/{template_id}         删除模版（仅 custom/）
  POST   /api/templates/from-instance         从运行实例导出模版
  POST   /api/templates/{template_id}/deploy  按模版一键部署（创建动态实例）
  GET    /api/templates/categories            分类与数量统计
"""
import logging
from pathlib import Path
from typing import Any, Dict

from aiohttp import web

from backend.core.templates import TemplateStore, normalize_category

logger = logging.getLogger("AMM.TemplateBridge")


def setup_routes(app: web.Application, manager):
    root = Path(manager.config.get("server", {}).get("amm_root")
                or __import__("os").environ.get("AMM_ROOT", "/amm")) / "templates"
    store = TemplateStore(str(root))

    class Handlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        async def list_templates(self, req):
            category = req.query.get("category", "")
            keyword = req.query.get("q", "")
            items = store.list(category=category, keyword=keyword)
            # 列表不带完整参数，减小体积
            brief = [{
                "id": t["id"], "name": t["name"], "category": t["category"],
                "description": t["description"], "tags": t["tags"],
                "engines": [e["engine"] for e in t["engines"]],
                "quantization": t["quantization"],
                "vram_hint_gb": t["vram_hint_gb"],
                "is_custom": t["is_custom"],
            } for t in items]
            return self._json({"total": len(brief), "templates": brief})

        async def categories(self, req):
            items = store.list()
            counts: Dict[str, int] = {}
            for t in items:
                counts[t["category"]] = counts.get(t["category"], 0) + 1
            return self._json({"total": len(items), "categories": counts})

        async def get_template(self, req):
            tid = req.match_info["template_id"]
            tpl = store.get(tid)
            if not tpl:
                return self._json({"error": f"模版不存在: {tid}"}, 404)
            return self._json(tpl)

        async def save_template(self, req):
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "请求体必须是对象"}, 400)
            try:
                saved = store.save(body, custom=True)
            except (ValueError, PermissionError) as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"success": True, "template": saved})

        async def delete_template(self, req):
            tid = req.match_info["template_id"]
            try:
                ok = store.delete(tid)
            except PermissionError as e:
                return self._json({"error": str(e)}, 403)
            if not ok:
                return self._json({"error": f"模版不存在: {tid}"}, 404)
            return self._json({"success": True, "id": tid})

        async def update_template(self, req):
            tid = req.match_info["template_id"]
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "请求体必须是对象"}, 400)
            try:
                saved = store.update(tid, body)
            except (ValueError, PermissionError) as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"success": True, "template": saved})

        async def duplicate_template(self, req):
            tid = req.match_info["template_id"]
            try:
                body = await req.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            try:
                saved = store.duplicate(tid,
                                        new_id=(body.get("id") or "").strip(),
                                        new_name=(body.get("name") or "").strip())
            except (ValueError, PermissionError) as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"success": True, "template": saved})

        async def from_instance(self, req):
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            model_id = (body.get("model_id") or body.get("model_uid") or "").strip()
            if not model_id:
                return self._json({"error": "缺少 model_id"}, 400)
            try:
                payload = store.from_instance(manager, model_id)
                saved = store.save(payload, custom=True)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"success": True, "template": saved})

        async def deploy(self, req):
            """按模版一键部署：优先用请求体覆盖，其次模版推荐引擎"""
            tid = req.match_info["template_id"]
            tpl = store.get(tid)
            if not tpl:
                return self._json({"error": f"模版不存在: {tid}"}, 404)
            try:
                body = await req.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}

            engines = tpl.get("engines") or []
            chosen: Dict[str, Any] = {}
            want_engine = (body.get("engine_type") or "").strip()
            if want_engine:
                chosen = next((e for e in engines if e["engine"] == want_engine), {})
            if not chosen:
                chosen = engines[0] if engines else {}
            if not chosen.get("engine"):
                return self._json({
                    "error": f"模版 {tid} 未配置可用引擎",
                    "hint": "请在模版 engines 中声明 engine",
                }, 400)

            params = dict(chosen.get("params") or {})
            params.update(body.get("parameters") or {})

            model_file = (body.get("model_file") or tpl.get("model_file")
                          or _first_file(tpl) or "").strip()

            spec = {
                "model_uid": (body.get("model_uid") or "").strip() or None,
                # 客户端显示的模型名：请求体优先，否则用模版名
                "model_name": (body.get("model_name") or "").strip() or tpl["name"],
                "model_type": tpl["category"],
                "engine_type": chosen["engine"],
                "engine_version": body.get("engine_version") or chosen.get("version") or "",
                "flavor": body.get("flavor") or chosen.get("flavor") or "",
                "model_file": model_file,
                "parameters": params,
                "replicas": int(body.get("replicas") or 1),
            }
            spec = {k: v for k, v in spec.items() if v is not None}

            result = await manager.launch_instance(spec)
            status = 400 if "error" in result else 200
            if status == 200:
                result["template_id"] = tid
            return self._json(result, status)

        async def discover_sources(self, req):
            """模版涉及的下载源（供前端「下载」按钮使用）"""
            tid = req.match_info["template_id"]
            tpl = store.get(tid)
            if not tpl:
                return self._json({"error": f"模版不存在: {tid}"}, 404)
            return self._json({
                "template_id": tid,
                "name": tpl["name"],
                "sources": tpl.get("sources") or [],
            })

    def _first_file(tpl: Dict[str, Any]) -> str:
        for s in tpl.get("sources") or []:
            files = s.get("files") or []
            if files:
                first = files[0]
                return first if isinstance(first, str) else str(first.get("path") or first.get("name") or "")
        return ""

    h = Handlers()
    app.router.add_get("/api/templates", h.list_templates)
    app.router.add_get("/api/templates/categories", h.categories)
    app.router.add_post("/api/templates", h.save_template)
    app.router.add_post("/api/templates/from-instance", h.from_instance)
    app.router.add_get("/api/templates/{template_id}", h.get_template)
    app.router.add_put("/api/templates/{template_id}", h.update_template)
    app.router.add_post("/api/templates/{template_id}/duplicate", h.duplicate_template)
    app.router.add_delete("/api/templates/{template_id}", h.delete_template)
    app.router.add_get("/api/templates/{template_id}/sources", h.discover_sources)
    app.router.add_post("/api/templates/{template_id}/deploy", h.deploy)
