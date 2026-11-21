"""
AMM v1.0 M3 模版系统测试（R2）
==============================
覆盖模版加载、分类过滤、保存、删除保护、从实例导出、按模版部署。
"""
import os
import shutil
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from backend.core.templates import TemplateStore, normalize_category
from backend.server import create_app


def _templates_root() -> Path:
    return Path(os.environ["AMM_ROOT"]) / "templates"


BUILTIN_CATS = {"chat", "embedding", "reranker", "asr", "tts", "ocr", "image", "video"}


class TestTemplateStore:

    def test_normalize_category_aliases(self):
        assert normalize_category("llm") == "chat"
        assert normalize_category("VLM") == "chat"
        assert normalize_category("t2i") == "image"
        assert normalize_category("rerank") == "reranker"
        assert normalize_category("unknown-x") == "unknown-x"

    def test_loads_builtin_templates(self):
        store = TemplateStore(str(_templates_root()))
        items = store.list()
        assert len(items) >= 8
        cats = {t["category"] for t in items}
        assert cats >= BUILTIN_CATS

    def test_template_shape(self):
        store = TemplateStore(str(_templates_root()))
        tpl = store.get("bge-m3")
        assert tpl is not None
        assert tpl["category"] == "embedding"
        assert tpl["engines"], "模版必须声明引擎"
        assert tpl["sources"], "模版必须声明下载源"
        engines = {e["engine"] for e in tpl["engines"]}
        assert "sentence_transformers" in engines

    def test_bge_m3_has_cpu_and_cuda_flavors(self):
        store = TemplateStore(str(_templates_root()))
        tpl = store.get("bge-m3")
        flavors = {e.get("flavor") for e in tpl["engines"] if e["engine"] == "sentence_transformers"}
        assert flavors == {"cpu", "cuda"}

    def test_filter_by_category(self):
        store = TemplateStore(str(_templates_root()))
        items = store.list(category="video")
        assert items
        assert all(t["category"] == "video" for t in items)

    def test_filter_by_keyword(self):
        store = TemplateStore(str(_templates_root()))
        items = store.list(keyword="rerank")
        assert items
        assert any("rerank" in t["name"].lower() or "rerank" in t["id"].lower() for t in items)

    def test_save_and_get_custom(self):
        store = TemplateStore(str(_templates_root()))
        saved = store.save({
            "name": "My Test Model",
            "category": "chat",
            "description": "custom tpl",
            "engines": [{"engine": "llama_cpp", "params": {"ctx_size": 4096}}],
        })
        assert saved["id"] == "my-test-model"
        assert saved["is_custom"] is True
        got = store.get("my-test-model")
        assert got["name"] == "My Test Model"
        store.delete("my-test-model")

    def test_save_requires_name_and_category(self):
        store = TemplateStore(str(_templates_root()))
        try:
            store.save({"category": "chat"})
            assert False, "应当抛出 ValueError"
        except ValueError:
            pass
        try:
            store.save({"name": "x"})
            assert False, "应当抛出 ValueError"
        except ValueError:
            pass

    def test_builtin_templates_not_deletable(self):
        store = TemplateStore(str(_templates_root()))
        try:
            store.delete("bge-m3")
            assert False, "内置模版应当受保护"
        except PermissionError:
            pass
        assert store.get("bge-m3") is not None


class TestTemplateAPI(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    async def test_list_templates(self):
        resp = await self.client.get("/api/templates")
        assert resp.status == 200
        data = await resp.json()
        assert data["total"] >= 8
        assert all("id" in t and "category" in t for t in data["templates"])

    async def test_list_templates_by_category(self):
        resp = await self.client.get("/api/templates?category=embedding")
        data = await resp.json()
        assert data["total"] >= 1
        assert all(t["category"] == "embedding" for t in data["templates"])

    async def test_categories_summary(self):
        resp = await self.client.get("/api/templates/categories")
        data = await resp.json()
        assert data["total"] >= 8
        assert "chat" in data["categories"]

    async def test_get_template_detail(self):
        resp = await self.client.get("/api/templates/qwen3.6-35b-a3b")
        assert resp.status == 200
        data = await resp.json()
        assert data["category"] == "chat"
        assert data["engines"][0]["engine"] == "llama_cpp"

    async def test_get_template_404(self):
        resp = await self.client.get("/api/templates/no-such-tpl")
        assert resp.status == 404

    async def test_save_and_delete_template(self):
        resp = await self.client.post("/api/templates", json={
            "name": "API Custom Tpl",
            "category": "embedding",
            "engines": [{"engine": "sentence_transformers", "flavor": "cpu"}],
        })
        assert resp.status == 200
        data = await resp.json()
        tid = data["template"]["id"]
        assert data["template"]["is_custom"] is True

        resp2 = await self.client.delete(f"/api/templates/{tid}")
        assert resp2.status == 200

        resp3 = await self.client.get(f"/api/templates/{tid}")
        assert resp3.status == 404

    async def test_delete_builtin_forbidden(self):
        resp = await self.client.delete("/api/templates/bge-m3")
        assert resp.status == 403

    async def test_template_sources(self):
        resp = await self.client.get("/api/templates/bge-m3/sources")
        assert resp.status == 200
        data = await resp.json()
        assert data["name"] == "BGE-M3"
        assert data["sources"]

    async def test_deploy_template_registers_instance(self):
        """按模版部署：沙箱无引擎二进制 -> 启动失败，但实例应已注册且端口已分配"""
        resp = await self.client.post(
            "/api/templates/bge-m3/deploy",
            json={"model_uid": "tpl-bge-m3", "flavor": "cpu"},
        )
        body = await resp.json()
        assert resp.status in (200, 400)
        assert body.get("model_uid") == "tpl-bge-m3"
        assert 18100 <= body.get("port", 0) <= 18499
        assert body.get("template_id") == "bge-m3"

        resp2 = await self.client.get("/api/instances/tpl-bge-m3")
        assert resp2.status == 200
        inst = await resp2.json()
        assert inst["engine_type"] == "sentence_transformers"
        assert inst["category"] == "embedding"
        assert inst["flavor"] == "cuda" or inst["flavor"] == "cpu"

    async def test_deploy_unknown_template(self):
        resp = await self.client.post("/api/templates/nope/deploy", json={})
        assert resp.status == 404

    async def test_from_instance_export(self):
        # 先有一个动态实例
        await self.client.post("/api/instances/launch", json={
            "model_uid": "export-src",
            "model_type": "chat",
            "engine_type": "llama_cpp",
            "flavor": "cuda",
            "parameters": {"ctx_size": 2048},
        })
        resp = await self.client.post("/api/templates/from-instance", json={"model_id": "export-src"})
        assert resp.status == 200
        data = await resp.json()
        tpl = data["template"]
        assert tpl["category"] == "chat"
        assert tpl["engines"][0]["engine"] == "llama_cpp"
        assert tpl["engines"][0]["params"]["ctx_size"] == 2048

    async def test_from_instance_unknown(self):
        resp = await self.client.post("/api/templates/from-instance", json={"model_id": "nope"})
        assert resp.status == 400
