"""
AMM v1.0 M5 OpenAI 全接口测试（R9）
====================================
覆盖新增端点与统一错误格式:
  /v1/models/{id}, /v1/completions, /v1/audio/translations,
  /v1/images/edits, /v1/files*, /v1/batches*
"""
from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app


class TestOpenAIExtensions(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    # ---------------- 统一错误格式 ----------------
    async def test_error_object_shape(self):
        resp = await self.client.get("/v1/models/nope-xyz")
        assert resp.status == 404
        data = await resp.json()
        err = data["error"]
        for key in ("message", "type", "param", "code"):
            assert key in err

    # ---------------- models ----------------
    async def test_get_local_model_detail(self):
        resp = await self.client.get("/v1/models/chat")
        assert resp.status == 200
        data = await resp.json()
        assert data["object"] == "model"
        assert data["id"] == "chat"
        assert data["owned_by"] == "amm-local"
        assert data["model_type"] == "chat"

    async def test_get_external_model_detail(self):
        await self.client.post("/api/providers/ext", json={
            "type": "openai", "models": ["ext-m-1"], "base_url": "http://x/v1",
        })
        resp = await self.client.get("/v1/models/ext-m-1")
        assert resp.status == 200
        data = await resp.json()
        assert data["owned_by"].startswith("provider:")

    async def test_get_model_not_found(self):
        resp = await self.client.get("/v1/models/zzz")
        assert resp.status == 404

    # ---------------- completions ----------------
    async def test_completions_requires_model(self):
        resp = await self.client.post("/v1/completions", json={"prompt": "hi"})
        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["param"] == "model"

    async def test_completions_requires_prompt(self):
        resp = await self.client.post("/v1/completions", json={"model": "chat"})
        assert resp.status == 400
        assert (await resp.json())["error"]["param"] == "prompt"

    async def test_completions_model_not_running(self):
        resp = await self.client.post("/v1/completions", json={"model": "chat", "prompt": "hi"})
        assert resp.status == 503
        data = await resp.json()
        assert data["error"]["code"] == "model_unavailable"

    async def test_completions_invalid_json(self):
        resp = await self.client.post("/v1/completions", data="not json",
                                      headers={"Content-Type": "application/json"})
        assert resp.status == 400

    # ---------------- translations ----------------
    async def test_translations_requires_asr_running(self):
        resp = await self.client.post("/v1/audio/translations")
        assert resp.status in (400, 503)
        data = await resp.json()
        assert "error" in data

    # ---------------- images edits ----------------
    async def test_images_edits_returns_explicit_unsupported(self):
        resp = await self.client.post("/v1/images/edits", json={})
        assert resp.status == 501
        data = await resp.json()
        assert data["error"]["code"] == "unsupported_endpoint"
        assert "images/edits" in data["error"]["message"]

    # ---------------- files ----------------
    async def test_files_empty_list(self):
        resp = await self.client.get("/v1/files")
        assert resp.status == 200
        data = await resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

    async def _upload(self, content: bytes = b'{"a":1}', filename: str = "b.json"):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", content, filename=filename,
                       content_type="application/octet-stream")
        form.add_field("purpose", "batch")
        return await self.client.post("/v1/files", data=form)

    async def test_file_upload_get_delete_lifecycle(self):
        resp = await self._upload()
        assert resp.status == 200
        meta = await resp.json()
        fid = meta["id"]
        assert meta["object"] == "file"
        assert meta["purpose"] == "batch"
        assert meta["bytes"] > 0
        assert meta["filename"] == "b.json"

        resp2 = await self.client.get(f"/v1/files/{fid}")
        assert resp2.status == 200
        assert (await resp2.json())["id"] == fid

        resp3 = await self.client.get("/v1/files")
        ids = [f["id"] for f in (await resp3.json())["data"]]
        assert fid in ids

        resp4 = await self.client.delete(f"/v1/files/{fid}")
        assert resp4.status == 200
        assert (await resp4.json())["deleted"] is True

        resp5 = await self.client.get(f"/v1/files/{fid}")
        assert resp5.status == 404

    async def test_upload_without_file_field(self):
        resp = await self.client.post("/v1/files", data={"purpose": "batch"})
        assert resp.status == 400
        assert (await resp.json())["error"]["param"] == "file"

    # ---------------- batches ----------------
    async def test_batch_lifecycle(self):
        from aiohttp import FormData
        form = FormData()
        form.add_field("file", b'{"x":1}', filename="in.jsonl",
                       content_type="application/octet-stream")
        form.add_field("purpose", "batch")
        fid = (await (await self.client.post("/v1/files", data=form)).json())["id"]

        resp = await self.client.post("/v1/batches", json={
            "input_file_id": fid, "endpoint": "/v1/chat/completions",
        })
        assert resp.status == 200
        batch = await resp.json()
        bid = batch["id"]
        assert batch["object"] == "batch"
        assert batch["status"] == "validating"
        assert batch["input_file_id"] == fid

        resp2 = await self.client.get(f"/v1/batches/{bid}")
        assert (await resp2.json())["id"] == bid

        resp3 = await self.client.get("/v1/batches")
        assert any(b["id"] == bid for b in (await resp3.json())["data"])

        resp4 = await self.client.post(f"/v1/batches/{bid}/cancel")
        assert (await resp4.json())["status"] == "cancelled"

    async def test_batch_requires_input_file(self):
        resp = await self.client.post("/v1/batches", json={})
        assert resp.status == 400
        assert (await resp.json())["error"]["param"] == "input_file_id"

    async def test_batch_unknown_file(self):
        resp = await self.client.post("/v1/batches", json={"input_file_id": "file-nope"})
        assert resp.status == 404

    async def test_batch_not_found(self):
        resp = await self.client.get("/v1/batches/batch-nope")
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == "batch_not_found"


class TestExistingEndpointsStillIntact(AioHTTPTestCase):
    """确认 R9 新增端点没有破坏 v0.9 已有契约"""

    async def get_application(self):
        return create_app()

    async def test_v1_models_list(self):
        """只列出运行中的模型。

        v1.4 起不再无条件暴露 chat/embedding/... 这些占位场景 id ——
        实例 stopped 时客户端会误当成可用模型去调用。
        这里断言：返回结构正确、且每条都是 running。
        """
        resp = await self.client.get("/v1/models")
        assert resp.status == 200
        data = await resp.json()
        assert data["object"] == "list"
        for m in data["data"]:
            assert m["object"] == "model"
            assert m["owned_by"]
            # 本地实例必须 running；外部 provider 汇聚的模型没有 status 字段
            # （它们是否可用由 provider 侧决定），这里只校验本地实例。
            if m.get("status") is not None:
                assert m["status"] == "running"

    async def test_chat_route_exists(self):
        resp = await self.client.post("/v1/chat/completions", json={"model": "chat", "messages": []})
        assert resp.status != 404

    async def test_embeddings_route_exists(self):
        resp = await self.client.post("/v1/embeddings", json={"model": "embedding", "input": "x"})
        assert resp.status != 404

    async def test_rerank_route_exists(self):
        resp = await self.client.post("/v1/rerank",
                                      json={"model": "reranker", "query": "q", "documents": ["a"]})
        assert resp.status != 404

    async def test_images_and_videos_routes_exist(self):
        assert (await self.client.post("/v1/images/generations", json={})).status != 404
        assert (await self.client.post("/v1/videos/generations", json={})).status != 404
