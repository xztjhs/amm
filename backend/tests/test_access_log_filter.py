"""访问日志降噪：前端健康轮询不能把日志写爆。

背景（2026-11-17）：entrypoint 用 `nohup ... >> amm_server.out` 启动，
stdout 不轮转；root logger 的 StreamHandler 是 INFO 级，aiohttp.access 通过
0.5s 前端轮询把 amm_server.out 刷到 240MB+（≈150MB/天）。修法是把访问日志
独立轮转 + 过滤掉健康轮询。这里锁死过滤器行为，防止回归。
"""
import ast
import logging
import re
from pathlib import Path

import pytest

_SERVER_PY = Path(__file__).resolve().parents[1] / "server.py"


def _load_filter_cls():
    """只从 server.py 里取出过滤器类，不导入整个模块（避免起服务/读配置）。"""
    tree = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
    node = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "_PollNoiseFilter"),
        None,
    )
    assert node is not None, "server.py 里找不到 _PollNoiseFilter"
    mod = ast.Module(body=[node], type_ignores=[])
    ns = {"logging": logging, "re": re}
    exec(compile(mod, "<poll_filter>", "exec"), ns)
    return ns["_PollNoiseFilter"]


class _Req:
    def __init__(self, method: str, path: str):
        self.method = method
        self.path = path


def _record(method: str, path: str) -> logging.LogRecord:
    # aiohttp AccessLogger 传的 args 是 (request, response, time)
    return logging.LogRecord(
        "aiohttp.access", logging.INFO, "", 0, "%s", (_Req(method, path), object(), 0.1), None
    )


def _formatted_record(method: str, path: str) -> logging.LogRecord:
    """模拟 aiohttp 的真实行为：msg 已经格式化好，args 为空。

    AccessLogger.log 里是 `self.logger.info(self._log_format % tuple(values))`，
    不会留下 request 对象。历史 bug 就出在这里：只查 record.args 的过滤器
    会全部放行，导致降噪完全失效。
    """
    line = (f'<CLIENT_IP> [17/Nov/2026:23:33:05 +0800] '
            f'"{method} {path} HTTP/1.1" 200 1291 "http://<YOUR_HOST>:60008/" "UA"')
    return logging.LogRecord("aiohttp.access", logging.INFO, "", 0, line, None, None)


FLT = _load_filter_cls()


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/system"),
    ("GET", "/api/system?verbose=1"),
    ("GET", "/api/instances"),
    ("GET", "/api/instances/embedding"),
    ("GET", "/api/health"),
    ("GET", "/api/bridge/diffusers/status"),
])
def test_poll_requests_are_dropped(method, path):
    assert FLT().filter(_record(method, path)) is False, (
        f"{method} {path} 是前端健康轮询，不该写入 access.log"
    )


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/models"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/api/instances/tts/start"),
    ("PUT", "/api/instances/asr/model-file"),
    ("DELETE", "/api/instances/demo"),
    ("GET", "/api/engines/catalog"),
    ("GET", "/api/fm/list"),
])
def test_real_calls_are_kept(method, path):
    assert FLT().filter(_record(method, path)) is True, (
        f"{method} {path} 是真实调用，必须保留在 access.log"
    )


def test_filter_survives_unexpected_record():
    """没有 args（或 args 结构异常）时不能抛异常，必须放行。"""
    rec = logging.LogRecord("aiohttp.access", logging.INFO, "", 0, "plain", None, None)
    assert FLT().filter(rec) is True


@pytest.mark.parametrize("method,path,keep", [
    ("GET", "/api/system", False),
    ("GET", "/api/instances", False),
    ("GET", "/api/bridge/diffusers/status", False),
    ("GET", "/api/health", False),
    ("GET", "/api/instances/embedding", False),
    ("GET", "/v1/models", True),
    ("POST", "/v1/chat/completions", True),
    ("POST", "/api/instances/tts/start", True),
    ("GET", "/api/engines/catalog", True),
    ("GET", "/api/fm/list?path=/models", True),
])
def test_filter_works_on_preformatted_message(method, path, keep):
    """真实链路：msg 已格式化、args 为空时过滤仍必须生效。"""
    assert FLT().filter(_formatted_record(method, path)) is keep
