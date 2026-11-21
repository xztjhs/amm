"""
SSE 文本捕获
=============
流式（`stream: true`）响应是 SSE，中间没有完整 JSON，以前调用历史只能记
`output: "(stream)"`，用户在 Log Center 里看不到模型到底说了什么。

本模块提供一个增量解析器：把转发的 SSE 字节流喂进去，边转发边累积
delta 里的文本，流结束后取 `.text()` 写入调用历史。

同时兼容：
  - 标准 OpenAI delta：`choices[0].delta.content`
  - 推理模型：`delta.reasoning_content` / `delta.reasoning`
  - 旧式补全：`choices[0].text`
  - 个别 provider 直接回 JSON 而不是 SSE（没有 `data:` 前缀）

纯状态机，无 IO，方便单测。
"""
import json
from typing import List, Optional

# 只保留这么多字符，避免 router.json 被长回答撑爆
DEFAULT_LIMIT = 4000


class SSETextCapture:
    """把 SSE 字节流累积成可读文本。"""

    def __init__(self, limit: int = DEFAULT_LIMIT):
        self.limit = limit
        self._buf = b""            # 未成行的残留字节
        self._parts: List[str] = []
        self._size = 0
        self.done = False
        self.events = 0            # 见过的数据事件数，便于判断解析是否真的生效

    # ------------------------------------------------------------------
    def feed(self, chunk: bytes) -> None:
        """喂一段原始字节（可以是任意切片位置，内部自行处理半行）。"""
        if not chunk:
            return
        self._buf += chunk
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            line, self._buf = self._buf[:idx], self._buf[idx + 1:]
            self._handle_line(line.rstrip(b"\r"))

    def close(self) -> None:
        """流结束时把残留的不完整行也尝试解析一次。"""
        if self._buf:
            self._handle_line(self._buf.rstrip(b"\r"))
            self._buf = b""

    # ------------------------------------------------------------------
    def _handle_line(self, raw: bytes) -> None:
        if not raw:
            return
        try:
            line = raw.decode("utf-8", "replace").strip()
        except Exception:
            return
        if not line:
            return
        if line.startswith(":"):        # SSE 注释/心跳
            return
        if line.startswith("data:"):
            payload = line[5:].strip()
        elif line.startswith("{"):      # 有的 provider 直接回裸 JSON
            payload = line
        else:
            return                     # event: / id: / retry: 等
        if not payload or payload == "[DONE]":
            self.done = True
            return
        try:
            obj = json.loads(payload)
        except Exception:
            return
        self.events += 1
        self._absorb(obj)

    def _absorb(self, obj) -> None:
        if not isinstance(obj, dict):
            return
        # 兼容 Anthropic 风格的 content_block_delta
        delta = obj.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            self._push(delta.get("text"))
            return
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        first = choices[0] or {}
        d = first.get("delta") or first.get("message") or {}
        if isinstance(d, dict):
            for key in ("content", "reasoning_content", "reasoning", "text"):
                v = d.get(key)
                if isinstance(v, str) and v:
                    self._push(v)
                    return
        elif isinstance(d, str):        # 极少数实现直接给字符串
            self._push(d)
            return
        # 旧式 /v1/completions 把文本直接放在 choice 上：{"choices":[{"text":"..."}]}
        legacy = first.get("text")
        if isinstance(legacy, str) and legacy:
            self._push(legacy)

    def _push(self, text: Optional[str]) -> None:
        if not text:
            return
        if self._size >= self.limit:
            return
        room = self.limit - self._size
        piece = text[:room]
        self._parts.append(piece)
        self._size += len(piece)

    # ------------------------------------------------------------------
    def text(self) -> str:
        return "".join(self._parts)

    @property
    def truncated(self) -> bool:
        return self._size >= self.limit
