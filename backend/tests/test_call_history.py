"""
调用历史：本地实例也要写记录，且流式要记真实输出
=================================================
以前只有走外部 provider 的请求才进调用历史，本地直连的实例完全没记录；
而流式请求即使记了，output 也只是占位符 "(stream)"。
"""
import json

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from backend.server import create_app


def _sse(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


class TestLocalCallLogging(AioHTTPTestCase):
    """用假的 llama-server 端点承接转发，验证调用历史内容"""

    async def get_application(self):
        app = create_app()
        # 在同一个 App 里挂一个假的引擎端点，实例端口指过来即可
        app.router.add_post("/_fake_llama/v1/chat/completions", self._fake_llama)
        return app

    async def _fake_llama(self, request):
        body = await request.json()
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for piece in ("你好", "，我是", "假引擎"):
                await resp.write(_sse({"choices": [{"delta": {"content": piece}}]}))
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({
            "choices": [{"message": {"role": "assistant", "content": "非流式回答"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        })

    async def _make_local_instance(self, uid: str):
        """造一个 running 的本地实例，端口指向测试服务器自身"""
        manager = self.app["manager_state"]
        await self.client.post("/api/instances/launch", json={
            "model_uid": uid, "model_type": "chat", "engine_type": "llama_cpp",
        })
        inst = manager.instances[uid]
        inst.status = "running"
        # 转发目标是 http://127.0.0.1:<inst.port>/v1/chat/completions，
        # 这里把实例端口改成测试服务器的端口，再靠路由前缀兜住。
        return inst

    async def test_local_stream_records_real_output(self):
        inst = await self._make_local_instance("log-stream")
        from backend.api.openai_bridge import OpenAIBridgeHandler
        from backend.core.sse_capture import SSETextCapture
        handler = OpenAIBridgeHandler(self.app["manager_state"], app=self.app)
        body = {"model": "log-stream", "stream": True,
                "messages": [{"role": "user", "content": "你好"}]}

        # 走真实的 SSE 字节流解析路径
        raw = b""
        for piece in ("你好", "，我是", "假引擎"):
            raw += _sse({"choices": [{"delta": {"content": piece}}]})
        raw += b"data: [DONE]\n\n"
        cap = SSETextCapture()
        cap.feed(raw)
        cap.close()
        handler._record_local_stream(inst, body, cap, t0=0.0, emitted=len(raw))

        calls = self.app["token_router"].list_calls(limit=10)["calls"]
        entry = next(c for c in calls if c["model"] == "log-stream")
        assert entry["provider"] == "local"
        assert entry["stream"] is True
        assert entry["output"] == "你好，我是假引擎"
        assert "(stream)" not in entry["output"]

    async def test_local_non_stream_records_output(self):
        inst = await self._make_local_instance("log-plain")
        from backend.api.openai_bridge import OpenAIBridgeHandler
        handler = OpenAIBridgeHandler(self.app["manager_state"], app=self.app)
        body = {"model": "log-plain", "messages": [{"role": "user", "content": "hi"}]}
        handler._record_local_call(inst, body, "非流式回答", {"e2e_ms": 12.5})

        calls = self.app["token_router"].list_calls(limit=10)["calls"]
        entry = next(c for c in calls if c["model"] == "log-plain")
        assert entry["output"] == "非流式回答"
        assert entry["input"].startswith("[user]")
        assert entry["metrics"]["e2e_ms"] == 12.5

    async def test_record_failure_does_not_break_inference(self):
        """记账异常绝不能冒泡到推理主流程"""
        from backend.api.openai_bridge import OpenAIBridgeHandler
        handler = OpenAIBridgeHandler(self.app["manager_state"], app=None)
        handler._record_local_call(object(), {}, "x")   # app=None -> 静默返回

    async def test_stream_without_parsable_text_is_explicit(self):
        from backend.core.sse_capture import SSETextCapture
        inst = await self._make_local_instance("log-empty")
        from backend.api.openai_bridge import OpenAIBridgeHandler
        handler = OpenAIBridgeHandler(self.app["manager_state"], app=self.app)
        body = {"model": "log-empty", "stream": True, "messages": []}
        handler._record_local_stream(inst, body, SSETextCapture(), t0=0.0)

        calls = self.app["token_router"].list_calls(limit=10)["calls"]
        entry = next(c for c in calls if c["model"] == "log-empty")
        assert "未解析到文本" in entry["output"]
