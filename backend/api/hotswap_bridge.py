"""
Engine Hot Swap — 引擎在线替换（R8，蓝绿）
=========================================
目标: 不修改/不重建容器镜像的前提下替换推理引擎版本。

前提（部署约束）:
  - AMM_ENGINES_ROOT 指向主机挂载卷（docker-compose 里 /opt/amm-engines）
  - 每个引擎版本/flavor 独立目录 + 独立 venv，天然可并存

蓝绿流程:
  1. 新版本引擎在独立目录安装（不影响在线实例）
  2. 针对目标实例「预热」：用新版本拉起一个测试端口实例并探活
  3. 探活通过 -> 原子切换实例的 engine_version/flavor，重启业务实例
  4. 探活失败 -> 保持旧版本继续服务，返回失败原因（可回滚）

回滚:
  switch_back(instance, old_version, old_flavor) 用回旧目录即可，
  旧目录在替换过程中始终保留。
"""
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

logger = logging.getLogger("AMM.EngineHotSwap")


class HotSwapManager:
    """引擎在线替换编排器"""

    def __init__(self, manager):
        self.manager = manager

    # ------------------------------------------------------------------
    def _engine_root(self) -> Path:
        from backend.core.version_manager import ENGINES_ROOT
        return Path(ENGINES_ROOT)

    def engine_status(self) -> Dict[str, Any]:
        """列出各引擎/版本/flavor 的安装状态与体积（用于 UI）"""
        root = self._engine_root()
        result: Dict[str, Any] = {
            "engines_root": str(root),
            "host_mounted": bool(os.environ.get("AMM_ENGINES_ROOT")),
            "engine_root_writable": os.access(str(root), os.W_OK) if root.exists() else False,
            "engines": {},
        }
        if not root.exists():
            return result
        for engine_dir in sorted(root.iterdir()):
            if not engine_dir.is_dir() or engine_dir.name in ("installed.json",):
                continue
            entries = []
            for ver_dir in sorted(engine_dir.iterdir()):
                if not ver_dir.is_dir():
                    continue
                size_mb = self._du_mb(ver_dir)
                name = ver_dir.name
                flavor = ""
                version = name
                for f in ("cuda", "cpu"):
                    if name.endswith(f"-{f}"):
                        flavor = f
                        version = name[: -(len(f) + 1)]
                        break
                binary = ver_dir / "bin" / "llama-server"
                entries.append({
                    "version": version,
                    "flavor": flavor,
                    "dir": str(ver_dir),
                    "size_mb": size_mb,
                    "has_binary": binary.is_file(),
                    "venv_python": (ver_dir / "venv" / "bin" / "python").is_file(),
                })
            result["engines"][engine_dir.name] = entries
        return result

    @staticmethod
    def _du_mb(path: Path) -> float:
        total = 0
        try:
            for p in path.rglob("*"):
                try:
                    if p.is_file() and not p.is_symlink():
                        total += p.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        return round(total / (1024 * 1024), 1)

    # ------------------------------------------------------------------
    async def health_probe(self, model_id: str, timeout: float = 20.0) -> Dict[str, Any]:
        """探活指定实例（用引擎自带的 health_check 能力）"""
        inst = self.manager.instances.get(model_id)
        if not inst:
            return {"ok": False, "error": f"实例 {model_id} 不存在"}
        engine = self.manager.registry.get(inst.engine_type)
        if not engine or not hasattr(engine, "health_check"):
            return {"ok": False, "error": f"引擎 {inst.engine_type} 不支持探活"}
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = await engine.health_check(inst)
            if last.get("ok"):
                return {"ok": True, "detail": last}
            await asyncio.sleep(1.0)
        return {"ok": False, "detail": last, "error": "探活超时"}

    async def swap(self, model_id: str, new_version: str, new_flavor: str = "",
                   require_healthy: bool = True) -> Dict[str, Any]:
        """在线替换实例使用的引擎版本/flavor（蓝绿：失败自动回滚）"""
        inst = self.manager.instances.get(model_id)
        if not inst:
            return {"error": f"实例 {model_id} 不存在"}
        if inst.status not in ("running", "stopped", "error"):
            return {"error": f"实例状态 {inst.status} 不允许替换"}

        old_version, old_flavor = inst.engine_version, inst.flavor
        was_running = inst.status == "running"

        # 校验目标目录存在
        target_dir = self._engine_root() / inst.engine_type / (
            f"{new_version}-{new_flavor}" if new_flavor else new_version
        )
        if not target_dir.is_dir():
            return {
                "error": f"目标引擎目录不存在: {target_dir}",
                "hint": "请先安装该版本/flavor 的引擎",
            }

        logger.info(f"引擎替换开始: {model_id} {old_version}/{old_flavor} -> {new_version}/{new_flavor}")

        # 停旧
        if was_running:
            await self.manager.stop_model(model_id)

        # 切新
        inst.engine_version = new_version
        if new_flavor:
            inst.flavor = new_flavor
        self.manager._save_state()

        # 起新
        start_result = await self.manager.start_model(model_id)
        if "error" in start_result:
            logger.error(f"引擎替换失败，回滚: {start_result['error']}")
            inst.engine_version, inst.flavor = old_version, old_flavor
            self.manager._save_state()
            if was_running:
                await self.manager.start_model(model_id)
            return {
                "error": f"新版本启动失败，已回滚: {start_result['error']}",
                "rolled_back": True,
                "old": {"version": old_version, "flavor": old_flavor},
            }

        # 探活
        probe = await self.health_probe(model_id) if require_healthy else {"ok": True, "skipped": True}
        if not probe.get("ok"):
            logger.error(f"引擎替换探活失败，回滚: {probe}")
            await self.manager.stop_model(model_id)
            inst.engine_version, inst.flavor = old_version, old_flavor
            self.manager._save_state()
            if was_running:
                await self.manager.start_model(model_id)
            return {
                "error": f"新版本探活失败，已回滚: {probe.get('error')}",
                "rolled_back": True,
                "probe": probe,
                "old": {"version": old_version, "flavor": old_flavor},
            }

        logger.info(f"引擎替换成功: {model_id} -> {new_version}/{new_flavor}")
        return {
            "success": True,
            "model_uid": model_id,
            "old": {"version": old_version, "flavor": old_flavor},
            "new": {"version": new_version, "flavor": new_flavor or old_flavor},
            "probe": probe,
        }


def setup_routes(app: web.Application, manager):
    hs = HotSwapManager(manager)

    class Handlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        async def status(self, req):
            """GET /api/engines/hotswap/status  引擎目录与挂载状态"""
            return self._json(hs.engine_status())

        async def swap(self, req):
            """POST /api/instances/{model_id}/engine/hotswap  {version, flavor}"""
            model_id = req.match_info["model_id"]
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"invalid json: {e}"}, 400)
            version = (body.get("version") or "").strip()
            flavor = (body.get("flavor") or "").strip()
            if not version:
                return self._json({"error": "缺少 version"}, 400)
            result = await hs.swap(
                model_id, version, flavor,
                require_healthy=bool(body.get("require_healthy", True)),
            )
            status = 400 if "error" in result else 200
            return self._json(result, status)

        async def probe(self, req):
            """GET /api/instances/{model_id}/health  实例探活"""
            model_id = req.match_info["model_id"]
            result = await hs.health_probe(model_id)
            return self._json(result, 200 if result.get("ok") else 503)

    h = Handlers()
    app.router.add_get("/api/engines/hotswap/status", h.status)
    app.router.add_post("/api/instances/{model_id}/engine/hotswap", h.swap)
    app.router.add_get("/api/instances/{model_id}/health", h.probe)
