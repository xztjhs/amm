"""
AMM v1.0 M1 动态实例测试
=========================
覆盖 running-model 模式的核心能力：
  - 动态实例注册（launch）
  - 端口自动分配（不与 9 类固定场景冲突）
  - 实例注销（terminate）与固定场景保护
  - running-models 列表
  - SQLite 持久化与重启恢复
"""
import pytest
from aiohttp.test_utils import AioHTTPTestCase

from backend.core.port_allocator import PortAllocator
from backend.core.instance_store import InstanceStore
from backend.server import create_app


class TestPortAllocator:
    """端口分配器单元测试"""

    def test_allocate_sequential(self):
        alloc = PortAllocator(20000, 20010)
        p1 = alloc.allocate()
        p2 = alloc.allocate()
        assert p1 == 20000
        assert p2 == 20001
        assert p1 != p2

    def test_reserved_not_allocated(self):
        alloc = PortAllocator(20000, 20010, reserved=[20000, 20001])
        p = alloc.allocate()
        assert p == 20002

    def test_release_frees_port(self):
        alloc = PortAllocator(20000, 20010)
        p = alloc.allocate()
        alloc.release(p)
        assert alloc.allocate() == p

    def test_preferred_port(self):
        alloc = PortAllocator(20000, 20010)
        assert alloc.allocate(preferred=20005) == 20005

    def test_preferred_out_of_range_falls_back(self):
        alloc = PortAllocator(20000, 20010)
        p = alloc.allocate(preferred=99999)
        assert p == 20000

    def test_pool_exhaustion(self):
        alloc = PortAllocator(20000, 20001)
        assert alloc.allocate() == 20000
        assert alloc.allocate() == 20001
        assert alloc.allocate() is None

    def test_reserved_port_not_reallocated(self):
        alloc = PortAllocator(20000, 20010)
        alloc.reserve(20003)
        allocated = [alloc.allocate() for _ in range(3)]
        assert 20003 not in allocated


class TestInstanceStore:
    """SQLite 实例存储单元测试"""

    def test_upsert_and_get(self, tmp_path):
        store = InstanceStore(str(tmp_path / "data" / "amm.db"))
        store.upsert("uid-1", "chat", 18100, "running", True,
                     {"model_id": "uid-1", "name": "demo", "parameters": {"ctx_size": 4096}})
        row = store.get("uid-1")
        assert row is not None
        assert row["category"] == "chat"
        assert row["port"] == 18100
        assert row["is_dynamic"] is True
        assert row["data"]["parameters"]["ctx_size"] == 4096

    def test_update_existing(self, tmp_path):
        store = InstanceStore(str(tmp_path / "amm.db"))
        store.upsert("uid-1", "chat", 18100, "running", True, {"a": 1})
        store.upsert("uid-1", "chat", 18101, "stopped", True, {"a": 2})
        row = store.get("uid-1")
        assert row["port"] == 18101
        assert row["status"] == "stopped"

    def test_delete(self, tmp_path):
        store = InstanceStore(str(tmp_path / "amm.db"))
        store.upsert("uid-1", "chat", 18100, "running", True, {})
        store.delete("uid-1")
        assert store.get("uid-1") is None

    def test_list_dynamic_only(self, tmp_path):
        store = InstanceStore(str(tmp_path / "amm.db"))
        store.upsert("legacy", "chat", 18081, "stopped", False, {})
        store.upsert("dyn", "chat", 18100, "running", True, {})
        rows = store.list_all(dynamic_only=True)
        assert [r["uid"] for r in rows] == ["dyn"]
        assert len(store.list_all()) == 2


class TestDynamicInstanceAPI(AioHTTPTestCase):
    """动态实例 API 契约"""

    async def get_application(self):
        return create_app()

    @property
    def manager(self):
        return self.app["manager_state"]

    async def test_launch_missing_category(self):
        resp = await self.client.post("/api/instances/launch", json={"engine_type": "llama_cpp"})
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_launch_missing_engine(self):
        resp = await self.client.post("/api/instances/launch", json={"model_type": "chat"})
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_launch_unknown_engine(self):
        resp = await self.client.post(
            "/api/instances/launch",
            json={"model_type": "chat", "engine_type": "not_exist"},
        )
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_launch_category_mismatch(self):
        """diffusers 不支持 chat 类别"""
        resp = await self.client.post(
            "/api/instances/launch",
            json={"model_type": "chat", "engine_type": "diffusers"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data
        assert "supported" in data

    async def test_launch_duplicate_uid(self):
        resp = await self.client.post(
            "/api/instances/launch",
            json={"model_uid": "chat", "model_type": "chat", "engine_type": "llama_cpp"},
        )
        assert resp.status == 400
        assert "已存在" in (await resp.json())["error"]

    async def test_launch_registers_instance_and_allocates_port(self):
        """沙箱内无 llama.cpp 二进制 -> 启动会失败，但实例定义应已注册且端口已分配"""
        resp = await self.client.post(
            "/api/instances/launch",
            json={
                "model_uid": "chat-dyn-1",
                "model_type": "chat",
                "engine_type": "llama_cpp",
                "model_file": "fake-chat.gguf",
                "parameters": {"ctx_size": 8192},
            },
        )
        assert resp.status == 400  # 启动失败（无二进制）
        body = await resp.json()
        assert "error" in body
        assert body["model_uid"] == "chat-dyn-1"
        assert 18100 <= body["port"] <= 18499

        # 实例已出现在注册表
        resp2 = await self.client.get("/api/instances/chat-dyn-1")
        assert resp2.status == 200
        inst = await resp2.json()
        assert inst["is_dynamic"] is True
        assert inst["category"] == "chat"
        assert inst["parameters"]["ctx_size"] == 8192

    async def test_two_dynamic_instances_get_distinct_ports(self):
        for uid in ("dyn-a", "dyn-b"):
            await self.client.post(
                "/api/instances/launch",
                json={"model_uid": uid, "model_type": "chat", "engine_type": "llama_cpp"},
            )
        resp = await self.client.get("/api/instances")
        data = await resp.json()
        pa, pb = data["dyn-a"]["port"], data["dyn-b"]["port"]
        assert pa != pb
        # 且不与固定场景端口冲突
        legacy_ports = {v["port"] for k, v in data.items() if not v["is_dynamic"]}
        assert pa not in legacy_ports and pb not in legacy_ports

    async def test_terminate_dynamic_instance(self):
        await self.client.post(
            "/api/instances/launch",
            json={"model_uid": "dyn-term", "model_type": "chat", "engine_type": "llama_cpp"},
        )
        resp = await self.client.delete("/api/instances/dyn-term")
        assert resp.status == 200
        assert (await resp.json()).get("success") is True

        resp2 = await self.client.get("/api/instances/dyn-term")
        assert resp2.status == 404

    async def test_terminate_legacy_instance_rejected(self):
        """固定场景不可注销，只能 stop"""
        resp = await self.client.delete("/api/instances/chat")
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_terminate_unknown(self):
        resp = await self.client.delete("/api/instances/nope")
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_running_models_listing(self):
        """手工把一个动态实例标记为 running，验证 running-models 列表"""
        await self.client.post(
            "/api/instances/launch",
            json={"model_uid": "dyn-run", "model_type": "chat", "engine_type": "llama_cpp"},
        )
        inst = self.manager.instances["dyn-run"]
        inst.status = "running"
        inst.pid = 4242
        inst.uptime_seconds = 12

        resp = await self.client.get("/api/running-models")
        assert resp.status == 200
        data = await resp.json()
        uids = [m["model_uid"] for m in data["models"]]
        assert "dyn-run" in uids
        entry = next(m for m in data["models"] if m["model_uid"] == "dyn-run")
        for key in ("model_uid", "model_name", "model_type", "engine_type", "port",
                    "status", "replicas", "uptime_seconds", "is_dynamic"):
            assert key in entry, f"running-models 缺少字段 {key}"
        assert entry["status"] == "running"

    async def test_dynamic_instance_persisted_to_sqlite(self):
        await self.client.post(
            "/api/instances/launch",
            json={"model_uid": "dyn-persist", "model_type": "embedding", "engine_type": "llama_cpp"},
        )
        row = self.manager.store.get("dyn-persist")
        assert row is not None
        assert row["is_dynamic"] is True
        assert row["category"] == "embedding"
        assert row["data"]["model_id"] == "dyn-persist"

    async def test_dynamic_instance_restored_after_manager_restart(self):
        """模拟服务重启：新建 ModelManager 后动态实例定义应被恢复"""
        await self.client.post(
            "/api/instances/launch",
            json={
                "model_uid": "dyn-restore",
                "model_type": "chat",
                "engine_type": "llama_cpp",
                "parameters": {"ctx_size": 2048},
            },
        )
        port = self.manager.instances["dyn-restore"].port

        # 新建一个 manager（共享同一个 SQLite / AMM_ROOT 沙箱）
        from backend.core.model_manager import ModelManager
        manager2 = ModelManager(self.manager.config_path)
        assert "dyn-restore" in manager2.instances
        restored = manager2.instances["dyn-restore"]
        assert restored.is_dynamic is True
        assert restored.port == port
        assert restored.parameters["ctx_size"] == 2048
        assert restored.status == "stopped"  # 进程未存活

    async def test_legacy_instances_still_work_after_dynamic_support(self):
        """兼容层：9 类固定场景不受动态实例机制影响"""
        resp = await self.client.get("/api/instances")
        data = await resp.json()
        for model_id in ("chat", "embedding", "asr", "tts", "reranker", "ocr", "t2i", "t2v", "i2v"):
            assert model_id in data
            assert data[model_id]["is_dynamic"] is False
