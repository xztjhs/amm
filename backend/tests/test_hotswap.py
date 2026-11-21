"""
AMM v1.0 M2 引擎在线替换（R8 蓝绿）测试
========================================
沙箱里没有真实引擎二进制，因此重点验证:
  - 引擎目录状态接口
  - 目标目录不存在时拒绝替换
  - 启动失败时回滚（引擎版本/flavor 恢复原值）
  - 探活接口的契约
"""
from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app


class TestEngineHotSwap(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    @property
    def manager(self):
        return self.app["manager_state"]

    async def test_status_endpoint(self):
        resp = await self.client.get("/api/engines/hotswap/status")
        assert resp.status == 200
        data = await resp.json()
        assert "engines_root" in data
        assert "engines" in data
        assert isinstance(data["engines"], dict)

    async def test_swap_rejects_missing_target_dir(self):
        resp = await self.client.post(
            "/api/instances/chat/engine/hotswap",
            json={"version": "no-such-version", "flavor": "cuda"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "不存在" in data["error"]
        assert "hint" in data

    async def test_swap_requires_version(self):
        resp = await self.client.post(
            "/api/instances/chat/engine/hotswap",
            json={"flavor": "cuda"},
        )
        assert resp.status == 400

    async def test_swap_unknown_instance(self):
        resp = await self.client.post(
            "/api/instances/nope/engine/hotswap",
            json={"version": "x"},
        )
        assert resp.status == 400
        assert "不存在" in (await resp.json())["error"]

    async def test_swap_rolls_back_on_start_failure(self):
        """目标目录存在但无二进制 -> 启动失败 -> 回滚到原版本/flavor

        隔离要求:
          - 使用本用例唯一的实例 uid 与目标目录名，避免跨用例污染
          - 在 finally 里清理目录
          - 显式断言回滚后版本与 flavor 与替换前完全一致
        """
        await self.client.post(
            "/api/instances/launch",
            json={
                "model_uid": "swap-test",
                "model_type": "chat",
                "engine_type": "llama_cpp",
                "engine_version": "orig-v0",
                "flavor": "cuda",
            },
        )
        inst = self.manager.instances["swap-test"]
        old_version, old_flavor = inst.engine_version, inst.flavor
        assert old_version == "orig-v0"
        assert old_flavor == "cuda"

        from backend.core.version_manager import ENGINES_ROOT
        from pathlib import Path
        target = Path(ENGINES_ROOT) / "llama_cpp" / "swap-test-probe-cuda"
        target.mkdir(parents=True, exist_ok=True)

        try:
            resp = await self.client.post(
                "/api/instances/swap-test/engine/hotswap",
                json={"version": "swap-test-probe", "flavor": "cuda"},
            )
            assert resp.status == 400
            data = await resp.json()
            assert data.get("rolled_back") is True
            assert "回滚" in data["error"]
            assert data["old"] == {"version": old_version, "flavor": old_flavor}

            # 回滚后版本/flavor 必须与替换前完全一致
            inst2 = self.manager.instances["swap-test"]
            assert inst2.engine_version == old_version
            assert inst2.flavor == old_flavor
            assert inst2.engine_version != "swap-test-probe"
        finally:
            import shutil
            shutil.rmtree(target, ignore_errors=True)

    async def test_swap_does_not_leave_target_dir_side_effects(self):
        """替换失败不应在引擎根目录留下其它实例可见的残留状态"""
        resp = await self.client.get("/api/engines/hotswap/status")
        data = await resp.json()
        # 基线沙箱里不应存在本用例伪造的目录
        for entries in data.get("engines", {}).values():
            for e in entries:
                assert "swap-test-probe" not in e["dir"]

    async def test_health_probe_contract(self):
        """未运行实例探活应返回 ok=False 且不崩溃"""
        resp = await self.client.get("/api/instances/chat/health")
        assert resp.status in (200, 503)
        data = await resp.json()
        assert "ok" in data

    async def test_health_probe_unknown_instance(self):
        resp = await self.client.get("/api/instances/nope/health")
        assert resp.status == 503
        assert (await resp.json())["ok"] is False
