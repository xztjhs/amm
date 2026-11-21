"""
AMM v1.0 M4 Router 测试（R3/R4）
================================
覆盖:
  - provider 注册/列表/删除/校验
  - 虚拟 key 签发/鉴权/吊销/配额
  - 路由决策（local / provider / 前缀强制 / 未找到）
  - 模型合集（本地 + 外部）
  - 用量记账与报表
"""
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import AioHTTPTestCase

from backend.core.router import RouterStore, TokenRouter, DEFAULT_COST_TABLE, _month
from backend.server import create_app


class TestRouterStore:

    def test_load_save_roundtrip(self, tmp_path):
        p = tmp_path / "router.json"
        s = RouterStore(str(p))
        s.data["providers"]["x"] = {"type": "openai", "base_url": "http://x/v1", "models": ["m1"]}
        s.save()
        s2 = RouterStore(str(p))
        assert s2.data["providers"]["x"]["models"] == ["m1"]

    def test_no_file_is_empty(self, tmp_path):
        s = RouterStore(str(tmp_path / "nope.json"))
        assert s.data["providers"] == {}
        assert s.data["keys"] == {}


class TestTokenRouterUnit:

    def _router(self, tmp_path):
        store = RouterStore(str(tmp_path / "router.json"))
        return TokenRouter(manager=None, store=store), store

    def test_upsert_provider_validates_name(self, tmp_path):
        r, _ = self._router(tmp_path)
        with pytest.raises(ValueError):
            r.upsert_provider("bad name!", {})

    def test_upsert_provider_and_list(self, tmp_path):
        r, _ = self._router(tmp_path)
        r.upsert_provider("myprov", {"type": "openai", "models": ["gpt-x"], "base_url": "http://a/v1"})
        items = r.list_providers()
        assert len(items) == 1
        assert items[0]["name"] == "myprov"
        assert items[0]["models"] == ["gpt-x"]

    def test_provider_key_masking(self, tmp_path):
        r, _ = self._router(tmp_path)
        r.upsert_provider("p1", {"type": "openai", "api_key": "sk-super-secret-value"})
        item = r.list_providers(reveal=True)[0]
        assert item["has_key"] is True
        assert "super-secret" not in item["api_key_masked"]
        assert item["api_key_masked"].startswith("sk-sup")

    def test_provider_key_from_env(self, tmp_path, monkeypatch):
        r, _ = self._router(tmp_path)
        r.upsert_provider("p2", {"type": "openai", "api_key_env": "MY_TEST_KEY"})
        monkeypatch.setenv("MY_TEST_KEY", "env-key-value")
        assert r.resolve_provider_key("p2") == "env-key-value"

    def test_issue_and_authenticate_key(self, tmp_path):
        r, _ = self._router(tmp_path)
        issued = r.issue_key({"name": "app1", "rpm_limit": 60})
        raw = issued["key"]
        assert raw.startswith("sk-amm-")
        ok, reason, cfg = r.authenticate(raw)
        assert ok is True
        assert cfg["name"] == "app1"

    def test_authenticate_rejects_bad_key(self, tmp_path):
        r, _ = self._router(tmp_path)
        r.issue_key({"name": "app1"})
        ok, reason, _ = r.authenticate("sk-amm-wrong")
        assert ok is False
        assert reason == "invalid-api-key"

    def test_authenticate_no_keys_allows(self, tmp_path):
        r, _ = self._router(tmp_path)
        ok, reason, _ = r.authenticate("")
        assert ok is True
        assert reason == "no-keys-configured"

    def test_revoke_key(self, tmp_path):
        r, _ = self._router(tmp_path)
        raw = r.issue_key({"name": "t"})["key"]
        assert r.revoke_key(raw) is True
        ok, _, _ = r.authenticate(raw)
        assert ok is False

    def test_quota_model_whitelist(self, tmp_path):
        r, _ = self._router(tmp_path)
        raw = r.issue_key({"name": "t", "allowed_models": ["m1"]})["key"]
        _, _, cfg = r.authenticate(raw)
        assert r.check_quota(cfg, "m1")[0] is True
        assert r.check_quota(cfg, "m2")[0] is False

    def test_quota_monthly_limit(self, tmp_path):
        r, store = self._router(tmp_path)
        raw = r.issue_key({"name": "t", "monthly_token_limit": 100})["key"]
        _, _, cfg = r.authenticate(raw)
        store.data["usage"] = {"key:" + cfg["key_id"]: {"monthly_tokens": 99, "month": _month()}}
        ok, why = r.check_quota(cfg, "any", est_tokens=10)
        assert ok is False
        assert "monthly" in why

    def test_usage_recording(self, tmp_path):
        r, _ = self._router(tmp_path)
        r.record_usage(None, "m1", "openai", 1000, 500)
        summary = r.usage_summary()
        assert summary["totals"]["requests"] == 1
        assert summary["totals"]["total_tokens"] == 1500
        assert summary["totals"]["cost_cny"] > 0

    def test_usage_local_is_free(self, tmp_path):
        r, _ = self._router(tmp_path)
        r.record_usage(None, "local-model", "local", 1000, 1000)
        summary = r.usage_summary()
        assert summary["totals"]["cost_cny"] == 0.0

    def test_cost_table_has_expected_providers(self):
        assert "openai" in DEFAULT_COST_TABLE
        assert "icompify" in DEFAULT_COST_TABLE


class TestRouterAPI(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    @property
    def router(self):
        return self.app["token_router"]

    async def test_provider_crud(self):
        resp = await self.client.post("/api/providers/testprov", json={
            "type": "openai", "models": ["gpt-x"], "base_url": "http://example/v1",
        })
        assert resp.status == 200

        resp = await self.client.get("/api/providers")
        data = await resp.json()
        names = [p["name"] for p in data["providers"]]
        assert "testprov" in names

        resp = await self.client.get("/api/providers/testprov")
        assert resp.status == 200
        assert (await resp.json())["type"] == "openai"

        resp = await self.client.delete("/api/providers/testprov")
        assert (await resp.json())["success"] is True

        resp = await self.client.get("/api/providers/testprov")
        assert resp.status == 404

    async def test_provider_default_base_url(self):
        await self.client.post("/api/providers/ds", json={"type": "deepseek", "models": ["deepseek-chat"]})
        resp = await self.client.get("/api/providers/ds")
        data = await resp.json()
        assert data["base_url"] == "https://api.deepseek.com/v1"

    async def test_provider_invalid_name(self):
        resp = await self.client.post("/api/providers/bad%20name", json={"type": "openai"})
        assert resp.status in (400, 404)

    async def test_issue_and_list_keys(self):
        resp = await self.client.post("/api/token-router/keys", json={"name": "app-x", "rpm_limit": 30})
        assert resp.status == 200
        data = await resp.json()
        assert data["key"].startswith("sk-amm-")

        resp2 = await self.client.get("/api/token-router/keys")
        data2 = await resp2.json()
        assert any(k["name"] == "app-x" for k in data2["keys"])
        # 列表只显示掩码
        assert all("..." in k["key"] or "***" in k["key"] for k in data2["keys"])

    async def test_revoke_key(self):
        raw = (await (await self.client.post("/api/token-router/keys",
                                             json={"name": "rev"})).json())["key"]
        resp = await self.client.delete(f"/api/token-router/keys/{raw}")
        assert (await resp.json())["success"] is True

    async def test_usage_endpoint(self):
        self.router.record_usage(None, "m", "openai", 10, 20)
        resp = await self.client.get("/api/token-router/usage")
        assert resp.status == 200
        data = await resp.json()
        assert "totals" in data and "rows" in data

    async def test_resolve_local_running(self):
        # 造一个 running 实例
        await self.client.post("/api/instances/launch", json={
            "model_uid": "r-local", "model_type": "chat", "engine_type": "llama_cpp",
        })
        self.app["manager_state"].instances["r-local"].status = "running"
        resp = await self.client.get("/api/router/resolve?model=r-local")
        data = await resp.json()
        assert data["target"] == "local"
        assert data["port"] == self.app["manager_state"].instances["r-local"].port

    async def test_resolve_local_prefix_force(self):
        await self.client.post("/api/instances/launch", json={
            "model_uid": "r-prefix", "model_type": "chat", "engine_type": "llama_cpp",
        })
        self.app["manager_state"].instances["r-prefix"].status = "running"
        resp = await self.client.get("/api/router/resolve?model=local/r-prefix")
        data = await resp.json()
        assert data["target"] == "local"
        assert data["reason"] == "prefix-local"

    async def test_resolve_provider(self):
        await self.client.post("/api/providers/pv", json={
            "type": "openai", "models": ["gpt-4o-mini"], "base_url": "http://x/v1",
        })
        resp = await self.client.get("/api/router/resolve?model=gpt-4o-mini")
        data = await resp.json()
        assert data["target"] == "provider"
        assert data["provider"] == "pv"

    async def test_resolve_provider_prefix(self):
        await self.client.post("/api/providers/pv2", json={"type": "openai", "base_url": "http://x/v1"})
        resp = await self.client.get("/api/router/resolve?model=provider/pv2/whatever")
        data = await resp.json()
        assert data["target"] == "provider"
        assert data["provider"] == "pv2"

    async def test_resolve_stopped_instance(self):
        resp = await self.client.get("/api/router/resolve?model=chat")
        data = await resp.json()
        assert data["target"] == "local-stopped"
        assert "error" in data

    async def test_resolve_unknown(self):
        resp = await self.client.get("/api/router/resolve?model=nope-xyz")
        data = await resp.json()
        assert data["target"] == "none"

    async def test_resolve_requires_model(self):
        resp = await self.client.get("/api/router/resolve")
        assert resp.status == 400

    async def test_merged_models(self):
        await self.client.post("/api/providers/pm", json={
            "type": "openai", "models": ["ext-model-1"], "base_url": "http://x/v1",
        })
        resp = await self.client.get("/api/router/models")
        data = await resp.json()
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "ext-model-1" in ids

    async def test_router_status(self):
        resp = await self.client.get("/api/router/status")
        assert resp.status == 200
        data = await resp.json()
        for key in ("providers", "keys", "local_running", "merged_models", "usage"):
            assert key in data

    async def test_relay_rejects_unknown_model(self):
        resp = await self.client.post("/api/router/relay", json={"model": "nope", "messages": []})
        # 已有 key 后鉴权强制，未带 key 应先 401
        assert resp.status in (401, 404)

    async def test_relay_unknown_model_with_valid_key(self):
        raw = (await (await self.client.post("/api/token-router/keys",
                                             json={"name": "probe"})).json())["key"]
        resp = await self.client.post(
            "/api/router/relay",
            json={"model": "definitely-not-a-model", "messages": []},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status == 404

    async def test_relay_requires_model(self):
        resp = await self.client.post("/api/router/relay", json={"messages": []})
        assert resp.status == 400

    async def test_relay_auth_rejects_when_keys_exist(self):
        await self.client.post("/api/token-router/keys", json={"name": "gated"})
        resp = await self.client.post(
            "/api/router/relay",
            json={"model": "chat", "messages": []},
            headers={"Authorization": "Bearer sk-amm-bogus"},
        )
        assert resp.status == 401

    async def test_relay_quota_model_whitelist(self):
        raw = (await (await self.client.post("/api/token-router/keys", json={
            "name": "wl", "allowed_models": ["allowed-model"],
        })).json())["key"]
        resp = await self.client.post(
            "/api/router/relay",
            json={"model": "other-model", "messages": []},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status == 429
