"""
AMM v1.0 M1 回归测试基线
========================
目的: 在改造 ModelManager（9 固定场景 -> 动态实例注册表）之前，锁定现有
9 类模型场景的 API 契约。改造后这些测试必须全部继续通过 —— 这是"旧 API
兼容层"的验收标准。

覆盖:
  A. 系统类端点
  B. 9 类场景实例的 CRUD/生命周期契约
  C. 引擎信息与版本
  D. 启动命令编排
  E. OpenAI 兼容端点契约
  F. 错误路径
"""
import json

import pytest
from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app

# 9 类固定场景的 model_id（v0.9 契约，M1 改造后仍须兼容）
NINE_MODEL_IDS = ["chat", "embedding", "asr", "tts", "reranker", "ocr", "t2i", "t2v", "i2v"]

NINE_CATEGORIES = {
    "chat": "chat",
    "embedding": "embedding",
    "asr": "asr",
    "tts": "tts",
    "reranker": "reranker",
    "ocr": "ocr",
    "t2i": "image",
    "t2v": "video",
    "i2v": "video",
}


class TestAMMRegression(AioHTTPTestCase):
    """AioHTTPTestCase 会为每个用例创建独立 app 实例"""

    async def get_application(self):
        return create_app()

    # ---------------- A. 系统类端点 ----------------

    async def test_health(self):
        resp = await self.client.get("/api/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    async def test_system_info(self):
        resp = await self.client.get("/api/system")
        assert resp.status == 200
        data = await resp.json()
        for key in ("cpu_percent", "cpu_count", "memory_total_gb", "disk_total_gb", "gpus"):
            assert key in data, f"缺少字段 {key}"

    async def test_settings(self):
        resp = await self.client.get("/api/settings")
        assert resp.status == 200
        data = await resp.json()
        for key in ("host", "port", "models_dir", "logs_dir", "version"):
            assert key in data, f"缺少字段 {key}"

    async def test_gpu(self):
        resp = await self.client.get("/api/gpu")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data.get("gpus"), list)

    async def test_server_logs(self):
        resp = await self.client.get("/api/logs/server")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data.get("logs"), list)

    async def test_docs_summary(self):
        resp = await self.client.get("/api/docs")
        assert resp.status == 200
        data = await resp.json()
        assert "docs" in data and isinstance(data["docs"], list)

    async def test_docs_content_path_traversal_blocked(self):
        resp = await self.client.get("/api/docs/..%2F..%2Fetc%2Fpasswd")
        assert resp.status in (400, 404)

    # ---------------- B. 9 类场景实例契约 ----------------

    async def test_instances_contains_nine_models(self):
        resp = await self.client.get("/api/instances")
        assert resp.status == 200
        data = await resp.json()
        assert set(data.keys()) == set(NINE_MODEL_IDS), f"实例集合与 9 类场景不一致: {sorted(data.keys())}"

    async def test_each_instance_shape(self):
        resp = await self.client.get("/api/instances")
        data = await resp.json()
        for model_id in NINE_MODEL_IDS:
            inst = data[model_id]
            for key in ("model_id", "name", "category", "port", "engine_type",
                        "status", "parameters", "selected_model_file", "request_count"):
                assert key in inst, f"{model_id} 缺少字段 {key}"
            assert inst["model_id"] == model_id
            assert inst["category"] == NINE_CATEGORIES[model_id], \
                f"{model_id} 类别应为 {NINE_CATEGORIES[model_id]}, 实际 {inst['category']}"

    async def test_get_single_instance(self):
        for model_id in NINE_MODEL_IDS:
            resp = await self.client.get(f"/api/instances/{model_id}")
            assert resp.status == 200
            data = await resp.json()
            assert data["model_id"] == model_id

    async def test_instance_not_found_404(self):
        resp = await self.client.get("/api/instances/no_such_model")
        assert resp.status == 404
        data = await resp.json()
        assert "error" in data

    async def test_ports_are_unique(self):
        resp = await self.client.get("/api/instances")
        data = await resp.json()
        ports = [inst["port"] for inst in data.values()]
        assert len(ports) == len(set(ports)), f"端口冲突: {ports}"

    async def test_update_parameters(self):
        resp = await self.client.put(
            "/api/instances/chat/parameters",
            json={"ctx_size": 4096},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data.get("success") is True
        resp2 = await self.client.get("/api/instances/chat")
        assert (await resp2.json())["parameters"]["ctx_size"] == 4096

    async def test_update_model_file(self):
        resp = await self.client.put(
            "/api/instances/embedding/model-file",
            json={"model_file": "fake-embedding.gguf"},
        )
        assert resp.status == 200
        resp2 = await self.client.get("/api/instances/embedding")
        assert (await resp2.json())["selected_model_file"] == "fake-embedding.gguf"

    async def test_config_models_listing(self):
        resp = await self.client.get("/api/config/models")
        assert resp.status == 200
        data = await resp.json()
        # server 段不对外暴露
        assert "server" not in data
        for key in ("chat_model", "embedding_model", "asr_model", "tts_model",
                    "reranker_model", "ocr_model", "t2i_model", "t2v_model", "i2v_model"):
            assert key in data, f"配置缺少 {key}"

    async def test_config_model_detail(self):
        resp = await self.client.get("/api/config/models/chat")
        assert resp.status == 200
        data = await resp.json()
        for key in ("models", "parameters", "available_engines", "port", "id"):
            assert key in data, f"缺少字段 {key}"
        assert data["id"] == "chat"

    async def test_config_model_detail_not_found(self):
        resp = await self.client.get("/api/config/models/nope")
        assert resp.status == 404

    async def test_stop_on_stopped_instance_is_safe(self):
        """对未运行的实例执行 stop: 应返回 success，不抛异常"""
        resp = await self.client.post("/api/instances/chat/stop")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("success") is True
        assert data.get("status") == "stopped"

    async def test_start_missing_engine_returns_error(self):
        """引擎二进制不存在时，start 应返回 error 而不是崩溃"""
        resp = await self.client.post("/api/instances/chat/start")
        assert resp.status == 200
        data = await resp.json()
        # 沙箱内没有 llama.cpp 二进制 -> 预期返回 error
        assert "error" in data or data.get("success") is True
        if "error" in data:
            # 失败后实例状态应为 error
            resp2 = await self.client.get("/api/instances/chat")
            assert (await resp2.json())["status"] == "error"

    async def test_advanced_settings_whitelist(self):
        """advanced 设置只接受白名单字段"""
        resp = await self.client.put(
            "/api/instances/t2i/advanced",
            json={"quant": "fp8"},
        )
        assert resp.status == 200
        assert (await resp.json()).get("success") is True

        resp2 = await self.client.put(
            "/api/instances/t2i/advanced",
            json={"category": "hacked"},
        )
        assert resp2.status == 400
        assert "error" in await resp2.json()

    async def test_advanced_settings_invalid_quant(self):
        resp = await self.client.put(
            "/api/instances/t2i/advanced",
            json={"quant": "int4"},
        )
        assert resp.status == 400

    # ---------------- C. 引擎 ----------------

    async def test_engines_info(self):
        resp = await self.client.get("/api/engines")
        assert resp.status == 200
        data = await resp.json()
        for engine_type in ("llama_cpp", "vllm", "diffusers"):
            assert engine_type in data, f"缺少引擎 {engine_type}"
            for key in ("display_name", "description", "supported_categories"):
                assert key in data[engine_type]

    async def test_engine_versions(self):
        resp = await self.client.get("/api/engines/versions")
        assert resp.status == 200
        data = await resp.json()
        assert "llama_cpp" in data
        assert isinstance(data["llama_cpp"], list)
        if data["llama_cpp"]:
            v = data["llama_cpp"][0]
            for key in ("version", "status", "install_path"):
                assert key in v

    async def test_select_engine_ok(self):
        resp = await self.client.put(
            "/api/instances/chat/engine",
            json={"engine_type": "vllm", "engine_version": "0.22.1"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data.get("success") is True
        assert data.get("engine_type") == "vllm"

    async def test_select_engine_unknown(self):
        resp = await self.client.put(
            "/api/instances/chat/engine",
            json={"engine_type": "not_exist"},
        )
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_select_engine_category_mismatch(self):
        """diffusers 不支持 chat 类别 -> 应被拒绝"""
        resp = await self.client.put(
            "/api/instances/chat/engine",
            json={"engine_type": "diffusers"},
        )
        assert resp.status == 400
        assert "error" in await resp.json()

    # ---------------- D. 启动命令编排 ----------------

    async def test_startup_command_lifecycle(self):
        # 默认无自定义命令
        resp = await self.client.get("/api/instances/chat/command")
        assert resp.status == 200
        data = await resp.json()
        assert data["model_id"] == "chat"
        assert data["has_custom"] is False

        # 保存
        resp = await self.client.put(
            "/api/instances/chat/command",
            json={"command": "echo hello"},
        )
        assert resp.status == 200
        assert (await resp.json()).get("success") is True

        resp = await self.client.get("/api/instances/chat/command")
        data = await resp.json()
        assert data["has_custom"] is True
        assert data["startup_command"] == "echo hello"

        # 清除
        resp = await self.client.delete("/api/instances/chat/command")
        assert resp.status == 200
        assert (await resp.json()).get("success") is True

        resp = await self.client.get("/api/instances/chat/command")
        assert (await resp.json())["has_custom"] is False

    async def test_command_preview(self):
        resp = await self.client.post("/api/instances/chat/command/preview")
        assert resp.status == 200
        data = await resp.json()
        # 沙箱内没有真实模型文件，可能返回 args，也可能返回 error，两种都要是合法 JSON
        assert "args" in data or "error" in data
        if "args" in data:
            assert isinstance(data["args"], list)
            assert "shell" in data

    async def test_command_preview_unknown_model(self):
        resp = await self.client.post("/api/instances/nope/command/preview")
        assert resp.status == 200
        assert "error" in await resp.json()

    # ---------------- E. OpenAI 兼容契约 ----------------

    async def test_v1_models_shape(self):
        resp = await self.client.get("/v1/models")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("object") == "list"
        assert isinstance(data.get("data"), list)

    async def test_v1_chat_completions_requires_model(self):
        resp = await self.client.post("/v1/chat/completions", json={"messages": []})
        # 缺 model 或实例未运行: 返回 4xx/503 + 结构化 error，不得 5xx 崩溃
        assert resp.status in (400, 404, 422, 503), resp.status
        data = await resp.json()
        assert isinstance(data, dict)
        assert "error" in data or "message" in data

    async def test_v1_embeddings_shape(self):
        resp = await self.client.post(
            "/v1/embeddings",
            json={"model": "embedding", "input": "hello"},
        )
        # 无运行实例时返回 503 + 结构化 error（当前契约），不得崩溃
        assert resp.status in (400, 404, 422, 503), resp.status
        data = await resp.json()
        assert isinstance(data, dict)
        assert "error" in data or "message" in data

    async def test_v1_rerank_shape(self):
        resp = await self.client.post(
            "/v1/rerank",
            json={"model": "reranker", "query": "q", "documents": ["a", "b"]},
        )
        assert resp.status in (400, 404, 422, 503), resp.status
        data = await resp.json()
        assert isinstance(data, dict)
        assert "error" in data or "message" in data

    # ---------------- F. 运维 ----------------

    async def test_settings_reload(self):
        resp = await self.client.post("/api/settings/reload")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("ok") is True

    async def test_settings_restart(self):
        resp = await self.client.post("/api/settings/restart")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("ok") is True

    async def test_cors_header(self):
        resp = await self.client.get("/api/health", headers={"Origin": "http://x"})
        assert resp.status == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
