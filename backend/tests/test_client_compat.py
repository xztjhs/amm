"""
客户端兼容性测试
=================
背景：Cherry Studio 等客户端会拿 /v1/models 的 id 去「模型名注册表」
做归一化模糊匹配（去掉 -14b/-0.6b 这类尺寸后缀），
  qwen3        → 命中 qwen3-14b，名称变 "Qwen3 14B" 且丢掉 embedding 能力
  qwen3-embedding-0.6b → 命中 qwen3-embedding-0-6b，名称与能力都正确
所以：
  1. 派生的 uid 必须自带模型身份与类别，且不带量化后缀
  2. /v1/models 每条本地模型必须带 name / description / capabilities
"""
from aiohttp.test_utils import AioHTTPTestCase

from backend.core.client_compat import (
    assess_model_id,
    derive_display_name,
    derive_uid,
    instance_meta,
)
from backend.core.engine import ModelInstance
from backend.server import create_app

EMB_FILE = "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"


class TestDeriveUid:
    def test_embedding_uid_keeps_identity_and_drops_quant(self):
        uid = derive_uid("embedding", EMB_FILE)
        assert uid == "qwen3-embedding-0.6b"
        # 量化后缀会让客户端注册表匹配不到
        assert "q8" not in uid and "gguf" not in uid
        # 类别身份必须出现在 uid 里
        assert "embedding" in uid

    def test_chat_uid_not_prefixed_with_category(self):
        uid = derive_uid("chat", "meta/Llama-3.1-8B-Instruct-Q4_K_M.gguf")
        assert uid.startswith("llama-3.1-8b")
        assert not uid.startswith("chat-")

    def test_reranker_uid_has_rerank_token(self):
        uid = derive_uid("reranker", "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf")
        assert "rerank" in uid
        assert "q8" not in uid

    def test_unknown_file_falls_back_to_category(self):
        assert derive_uid("embedding", "") == "embedding"

    def test_display_name_is_readable_and_not_uid(self):
        name = derive_display_name("embedding", EMB_FILE, fallback="x")
        # 名字里已经含 Embedding，不再重复追加类别标签
        assert name == "Qwen3-Embedding-0.6B"
        # 名字必须与 uid 不同，客户端才会保留我们的名字
        assert name != derive_uid("embedding", EMB_FILE)


class TestAssessModelId:
    def test_bare_family_name_warned(self):
        warnings = assess_model_id("qwen3", "embedding")
        assert warnings
        assert any("裸家族名" in w for w in warnings)

    def test_bare_family_name_warned_even_for_chat(self):
        # chat 类别也要提醒：qwen3 会被客户端认成 Qwen3 14B
        assert any("裸家族名" in w for w in assess_model_id("qwen3", "chat"))

    def test_missing_category_identity_warned(self):
        warnings = assess_model_id("my-model", "embedding")
        assert any("embedding" in w for w in warnings)

    def test_good_uid_has_no_warning(self):
        assert assess_model_id("qwen3-embedding-0.6b", "embedding") == []

    def test_quant_suffix_warned(self):
        warnings = assess_model_id("qwen3-embedding-0.6b-q8_0", "embedding")
        assert any("量化" in w for w in warnings)


class TestInstanceMeta:
    def test_meta_carries_capabilities(self):
        inst = ModelInstance(
            model_id="qwen3-embedding-0.6b", name="qwen3-embedding-0.6b",
            category="embedding", port=18100, selected_model_file=EMB_FILE,
        )
        meta = instance_meta(inst)
        assert meta["capabilities"] == ["embedding"]
        assert meta["name"] != inst.model_id
        assert "embedding" in meta["description"]

    def test_meta_keeps_distinct_instance_name(self):
        inst = ModelInstance(
            model_id="embedding", name="Embedding", category="embedding", port=18082,
        )
        assert instance_meta(inst)["name"] == "Embedding"


class TestModelsListExposesMetadata(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    async def test_dynamic_model_has_name_description_capabilities(self):
        await self.client.post("/api/instances/launch", json={
            "model_uid": "cc-embed", "model_type": "embedding",
            "engine_type": "llama_cpp", "model_file": EMB_FILE,
        })
        self.app["manager_state"].instances["cc-embed"].status = "running"

        resp = await self.client.get("/v1/models")
        assert resp.status == 200
        data = await resp.json()
        entry = next(m for m in data["data"] if m["id"] == "cc-embed")
        assert entry["name"] and entry["name"] != "cc-embed"
        assert entry["description"]
        assert entry["capabilities"] == ["embedding"]

    async def test_model_detail_has_same_metadata(self):
        await self.client.post("/api/instances/launch", json={
            "model_uid": "cc-embed-2", "model_type": "embedding",
            "engine_type": "llama_cpp", "model_file": EMB_FILE,
        })
        self.app["manager_state"].instances["cc-embed-2"].status = "running"

        resp = await self.client.get("/v1/models/cc-embed-2")
        assert resp.status == 200
        data = await resp.json()
        assert data["capabilities"] == ["embedding"]
        assert data["name"]

    async def test_launch_without_uid_derives_client_friendly_uid(self):
        resp = await self.client.post("/api/instances/launch", json={
            "model_type": "embedding", "engine_type": "llama_cpp", "model_file": EMB_FILE,
        })
        body = await resp.json()
        # 沙箱无二进制 -> 启动失败，但 uid 已派生
        assert body.get("model_uid") == "qwen3-embedding-0.6b"

    async def test_launch_bare_uid_returns_warning(self):
        resp = await self.client.post("/api/instances/launch", json={
            "model_uid": "qwen3b", "model_type": "embedding",
            "engine_type": "llama_cpp", "model_file": EMB_FILE,
        })
        body = await resp.json()
        assert body.get("warnings")
