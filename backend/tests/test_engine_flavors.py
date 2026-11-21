"""
AMM v1.0 M2 引擎 flavor 测试
=============================
覆盖:
  - EngineVersion.flavor 字段
  - llama.cpp cpu/cuda 双 flavor 版本清单
  - sentence_transformers 引擎注册与 cpu/cuda 双 flavor
  - 安装目录命名（{version}-{flavor}）
  - 引擎能力矩阵（sentence_transformers 支持 embedding/reranker）
"""
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app
from backend.engines.llama_cpp import LlamaCppEngine, _make_versions
from backend.engines.sentence_transformers import SentenceTransformersEngine
from backend.core.version_manager import VersionManager, InstallTask


class TestLlamaCppFlavors:

    def test_make_versions_has_cpu_and_cuda(self):
        versions = _make_versions("/tmp/engines")
        flavors = {v.flavor for v in versions}
        assert flavors == {"cpu", "cuda"}

    def test_flavor_install_paths_distinct(self):
        versions = _make_versions("/tmp/engines")
        paths = [v.install_path for v in versions]
        assert len(paths) == len(set(paths))
        cpu = next(v for v in versions if v.flavor == "cpu")
        cuda = next(v for v in versions if v.flavor == "cuda")
        assert cpu.install_path.endswith("latest-cpu")
        assert cuda.install_path.endswith("latest-cuda")
        assert cpu.metadata["cuda_build"] is False
        assert cuda.metadata["cuda_build"] is True

    @pytest.mark.asyncio
    async def test_engine_lists_both_flavors(self):
        engine = LlamaCppEngine("/tmp/engines")
        versions = await engine.list_installed_versions()
        flavors = {v.flavor for v in versions}
        assert "cpu" in flavors and "cuda" in flavors


class TestSentenceTransformersEngine:

    def test_supported_categories(self):
        engine = SentenceTransformersEngine("/tmp/engines")
        cats = engine.get_supported_categories()
        assert "embedding" in cats
        assert "reranker" in cats

    @pytest.mark.asyncio
    async def test_versions_have_both_flavors(self):
        engine = SentenceTransformersEngine("/tmp/engines")
        versions = await engine.list_installed_versions()
        flavors = {v.flavor for v in versions}
        assert flavors == {"cpu", "cuda"}
        # 每个版本都应有 cpu/cuda 两条
        assert len(versions) == 4

    @pytest.mark.asyncio
    async def test_build_command_cpu_flavor(self):
        from backend.core.engine import ModelInstance
        engine = SentenceTransformersEngine("/tmp/engines")
        inst = ModelInstance(
            model_id="emb1", name="emb1", category="embedding", port=18100,
            engine_type="sentence_transformers", flavor="cpu",
            selected_model_file="bge-m3",
        )
        cmd = await engine.build_command(
            {"port": 18100, "category": "embedding"}, inst, "/models", "0.0.0.0"
        )
        assert "--device" in cmd
        assert cmd[cmd.index("--device") + 1] == "cpu"
        assert "--port" in cmd
        assert cmd[cmd.index("--port") + 1] == "18100"

    @pytest.mark.asyncio
    async def test_build_command_cuda_flavor(self):
        from backend.core.engine import ModelInstance
        engine = SentenceTransformersEngine("/tmp/engines")
        inst = ModelInstance(
            model_id="emb2", name="emb2", category="embedding", port=18101,
            engine_type="sentence_transformers", flavor="cuda",
            selected_model_file="bge-m3", parameters={"batch_size": 64},
        )
        cmd = await engine.build_command(
            {"port": 18101, "category": "embedding"}, inst, "/models", "0.0.0.0"
        )
        assert "--device" not in cmd  # cuda flavor 不强制 device
        assert cmd[cmd.index("--batch-size") + 1] == "64"


class TestVersionManagerFlavor:

    def test_dir_name(self):
        assert VersionManager._dir_name("1.0.0", "") == "1.0.0"
        assert VersionManager._dir_name("1.0.0", "cpu") == "1.0.0-cpu"
        assert VersionManager._dir_name("1.0.0", "cuda") == "1.0.0-cuda"

    def test_install_task_has_flavor(self):
        task = InstallTask(engine_type="llama_cpp", version="latest", flavor="cpu")
        assert task.flavor == "cpu"


class TestEngineAPIWithFlavors(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    async def test_engine_versions_include_flavor_field(self):
        resp = await self.client.get("/api/engines/versions")
        assert resp.status == 200
        data = await resp.json()
        assert "llama_cpp" in data
        assert all("flavor" in v for v in data["llama_cpp"])

    async def test_sentence_transformers_registered(self):
        resp = await self.client.get("/api/engines")
        data = await resp.json()
        assert "sentence_transformers" in data
        st = data["sentence_transformers"]
        assert set(st["supported_categories"]) == {"embedding", "reranker"}

    async def test_launch_st_engine_cpu_flavor(self):
        """sentence_transformers + cpu flavor 应被接受并分配端口"""
        resp = await self.client.post(
            "/api/instances/launch",
            json={
                "model_uid": "st-cpu-1",
                "model_type": "embedding",
                "engine_type": "sentence_transformers",
                "flavor": "cpu",
                "model_file": "bge-m3",
            },
        )
        body = await resp.json()
        # 沙箱无真实 venv -> 启动失败，但应分配端口并注册
        assert body.get("model_uid") == "st-cpu-1"
        assert 18100 <= body.get("port", 0) <= 18499
        resp2 = await self.client.get("/api/instances/st-cpu-1")
        inst = await resp2.json()
        assert inst["flavor"] == "cpu"
        assert inst["engine_type"] == "sentence_transformers"

    async def test_launch_st_engine_rejects_chat(self):
        resp = await self.client.post(
            "/api/instances/launch",
            json={"model_type": "chat", "engine_type": "sentence_transformers"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "supported" in data
        assert set(data["supported"]) == {"embedding", "reranker"}
