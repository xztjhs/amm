"""
SSE 文本捕获测试
=================
流式调用以前在 Log Center 里只能看到 `output: "(stream)"`。
SSETextCapture 边转发边解析 delta，流结束后给出真正的输出文本。
"""
import json

from backend.core.sse_capture import SSETextCapture


def _sse(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def _delta(text, key="content") -> bytes:
    return _sse({"choices": [{"index": 0, "delta": {key: text}}]})


class TestSSETextCapture:
    def test_accumulates_content_deltas(self):
        cap = SSETextCapture()
        cap.feed(_delta("你好"))
        cap.feed(_delta("，世界"))
        cap.feed(b"data: [DONE]\n\n")
        assert cap.text() == "你好，世界"
        assert cap.events == 2
        assert cap.done is True

    def test_handles_chunk_split_mid_line(self):
        """网络分片可能把一行 SSE 切成两半"""
        cap = SSETextCapture()
        raw = _delta("abcdef")
        cap.feed(raw[:10])
        cap.feed(raw[10:])
        assert cap.text() == "abcdef"

    def test_reasoning_content_captured(self):
        cap = SSETextCapture()
        cap.feed(_delta("让我想想", key="reasoning_content"))
        cap.feed(_delta("答案是 42"))
        assert cap.text() == "让我想想答案是 42"

    def test_legacy_completion_text(self):
        cap = SSETextCapture()
        cap.feed(_sse({"choices": [{"text": "legacy"}]}))
        assert cap.text() == "legacy"

    def test_anthropic_text_delta(self):
        cap = SSETextCapture()
        cap.feed(_sse({"type": "content_block_delta",
                       "delta": {"type": "text_delta", "text": "hi"}}))
        assert cap.text() == "hi"

    def test_ignores_comments_and_event_lines(self):
        cap = SSETextCapture()
        cap.feed(b": ping\n\nevent: message\n")
        cap.feed(_delta("ok"))
        assert cap.text() == "ok"
        assert cap.events == 1

    def test_bare_json_line_without_data_prefix(self):
        cap = SSETextCapture()
        raw = json.dumps({"choices": [{"delta": {"content": "bare"}}]}).encode()
        cap.feed(raw + b"\n")
        assert cap.text() == "bare"
    def test_truncates_at_limit(self):
        cap = SSETextCapture(limit=10)
        cap.feed(_delta("0123456789abcdef"))
        assert cap.text() == "0123456789"
        assert cap.truncated is True

    def test_close_flushes_partial_line(self):
        cap = SSETextCapture()
        cap.feed(b'data: {"choices":[{"delta":{"content":"tail"}}]}')  # 无换行
        assert cap.text() == ""
        cap.close()
        assert cap.text() == "tail"

    def test_empty_stream_has_no_events(self):
        cap = SSETextCapture()
        cap.feed(b"")
        assert cap.text() == ""
        assert cap.events == 0
