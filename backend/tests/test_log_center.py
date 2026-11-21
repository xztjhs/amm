"""
AMM v1.0 M1 日志中心测试（R5 一期）
====================================
覆盖日志聚合、级别/关键字过滤、来源列表与下载。
"""
import os
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app


def _logs_dir() -> Path:
    """与后端一致：AMM_ROOT/logs"""
    return Path(os.environ["AMM_ROOT"]) / "logs"


def _write(name: str, lines: list) -> Path:
    d = _logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class TestLogCenter(AioHTTPTestCase):

    async def get_application(self):
        return create_app()

    async def asyncSetUp(self):
        await super().asyncSetUp()
        _write("amm_server.log", [
            "2026-11-14 10:00:00,001 [INFO] AMM: 服务启动",
            "2026-11-14 10:00:05,120 [WARNING] AMM.Manager: 恢复状态失败",
            "2026-11-14 10:00:09,999 [ERROR] AMM.Engine: 引擎二进制缺失",
        ])
        _write("chat_server.log", [
            "2026-11-14 10:01:00,000 [INFO] llama: loading model",
            "llama_model_loader: loaded meta data",
            "2026-11-14 10:01:03,500 [ERROR] llama: failed to load tensor",
        ])

    async def test_sources_listing(self):
        resp = await self.client.get("/api/logs/center/sources")
        assert resp.status == 200
        data = await resp.json()
        assert "logs_dir" in data
        names = [f["name"] for f in data["server_log"]]
        assert "amm_server.log" in names
        assert "chat_server.log" in names
        # 9 类固定场景实例都应在清单里
        assert "chat" in data["instances"]

    async def test_query_all(self):
        resp = await self.client.get("/api/logs/center?source=all&lines=100")
        assert resp.status == 200
        data = await resp.json()
        assert data["total"] > 0
        sources = {e["source"] for e in data["entries"]}
        assert "server" in sources
        assert "chat" in sources

    async def test_query_level_filter(self):
        resp = await self.client.get("/api/logs/center?source=all&level=ERROR&lines=100")
        data = await resp.json()
        assert data["total"] >= 2
        assert all(e["level"] == "ERROR" for e in data["entries"])

    async def test_query_keyword_filter(self):
        resp = await self.client.get("/api/logs/center?source=all&q=tensor&lines=100")
        data = await resp.json()
        assert data["total"] == 1
        assert "tensor" in data["entries"][0]["message"]

    async def test_query_single_source(self):
        resp = await self.client.get("/api/logs/center?source=chat&lines=100")
        data = await resp.json()
        assert data["total"] == 3
        assert all(e["source"] == "chat" for e in data["entries"])

    async def test_parse_levels(self):
        resp = await self.client.get("/api/logs/center?source=server&lines=100")
        data = await resp.json()
        levels = [e["level"] for e in data["entries"]]
        assert "INFO" in levels
        assert "WARNING" in levels
        assert "ERROR" in levels
        # RAW 行（无时间戳/级别）应被识别
        resp2 = await self.client.get("/api/logs/center?source=chat&lines=100")
        data2 = await resp2.json()
        assert any(e["level"] == "RAW" for e in data2["entries"])

    async def test_time_sorted(self):
        resp = await self.client.get("/api/logs/center?source=all&lines=100")
        data = await resp.json()
        timed = [e["ts"] for e in data["entries"] if e["ts"]]
        assert timed == sorted(timed)

    async def test_missing_source_returns_empty(self):
        resp = await self.client.get("/api/logs/center?source=not_exist&lines=50")
        assert resp.status == 200
        assert (await resp.json())["total"] == 0

    async def test_download_unknown_404(self):
        resp = await self.client.get("/api/logs/center/download?source=nope")
        assert resp.status == 404

    async def test_download_server_log(self):
        resp = await self.client.get("/api/logs/center/download?source=server")
        assert resp.status == 200
        body = await resp.text()
        assert "服务启动" in body
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    async def test_lines_limit(self):
        resp = await self.client.get("/api/logs/center?source=all&lines=10")
        data = await resp.json()
        assert data["total"] <= 10
