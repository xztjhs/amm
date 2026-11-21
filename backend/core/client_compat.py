"""
Client Compatibility Hints
==========================
客户端（Cherry Studio / Open WebUI / AnythingLLM …）只认 `/v1/models` 的 `id`。
Cherry Studio 这类客户端还会拿 id 去「模型名注册表」（models.dev 风格）补全
名称与能力，匹配是**归一化模糊匹配**：去掉 `-14b/-32b/-0.6b` 这类参数尺寸后缀后
再比对。

于是：
  - id = `qwen3`         → 归一化后命中 `qwen3-14b`（先到者胜），名称变成
                           "Qwen3 14B"，能力变成 function-call/reasoning，
                           `embedding` 能力丢失 → 知识库里选不到。
  - id = `qwen3-embedding-0.6b` → 命中 `qwen3-embedding-0-6b`，名称与
                           `embedding` 能力都正确。

本模块不去复刻注册表匹配（那是客户端的实现），只提供：
  1. `derive_uid()`      从模型文件路径派生一个「注册表友好」的实例 uid
  2. `derive_display_name()` 从模型文件路径派生人类可读的模型名
  3. `assess_model_id()` 判断某个 uid 在客户端可能被认错的风险，返回提示

纯函数、无 IO，方便单测。
"""
import os
import re
from typing import Any, Dict, List, Optional

# 各类别在「模型 id」里可能出现的身份关键词（小写）。
# 命中任意一个，就认为这个 id 自带类别身份，客户端不容易认错。
CATEGORY_TOKENS: Dict[str, tuple] = {
    "chat": ("chat", "instruct", "llm"),
    "embedding": ("embedding", "embed", "bge", "gte", "e5-", "-m3", "vector"),
    "reranker": ("rerank",),
    "asr": ("asr", "whisper", "paraformer", "sensevoice"),
    "tts": ("tts", "cosyvoice", "vits"),
    "ocr": ("ocr", "dots", "paddleocr"),
    "image": ("image", "flux", "sdxl", "stable-diffusion"),
    "video": ("video", "wan", "hunyuan-video", "cogvideo"),
}

# 类别 → 客户端能力标签（尽量对齐 Cherry Studio 的 MODEL_CAPABILITY 取值，
# 其它客户端会忽略未知值，不会报错）。
CATEGORY_CAPABILITIES: Dict[str, List[str]] = {
    "chat": ["chat", "function-call"],
    "embedding": ["embedding"],
    "reranker": ["rerank"],
    "asr": ["audio-transcript"],
    "tts": ["audio-generation"],
    "ocr": ["image-recognition"],
    "image": ["image-generation"],
    "video": ["video-generation"],
}

# 文件/目录名里需要剥掉的尾巴：量化、导出格式、容器标记
_TAIL_NOISE = re.compile(
    r"(?:[-_.](?:gguf|ggml|bin|safetensors|fp16|bf16|fp8|fp32|"
    r"q\d+(?:_[a-z0-9]+)*|iq\d+(?:_[a-z0-9]+)*|int[48]|awq|gptq|"
    r"instruct|hf|unquantized))+$",
    re.IGNORECASE,
)

# 保留字母数字与 . - _；其余（空格、括号、中文）转成 -
_UID_KEEP = re.compile(r"[^a-z0-9._-]+")

# 路径里这些片段是目录/组织名，不是模型身份
_NON_MODEL_COMPONENTS = {
    "models", "model", "gguf", "ggufs", "ggml", "root", "weights",
    "meta", "qwen", "ggml-org", "thebloke", "bartowski", "unsloth",
    "lmstudio-community", "mradermacher", "huggingface",
}


def _clean_component(text: str) -> str:
    """把一个目录/文件名清成 uid 片段"""
    text = text.strip()
    prev = None
    while prev != text:          # 量化尾巴可能叠多层: -Q8_0-GGUF
        prev = text
        text = _TAIL_NOISE.sub("", text)
    text = text.lower()
    text = _UID_KEEP.sub("-", text)
    text = re.sub(r"[-_.]{2,}", "-", text).strip("-._")
    return text


def _model_identity(model_file: str) -> str:
    """从模型文件路径取「模型身份」片段。

    `Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf`
      → `qwen3-embedding-0.6b`（优先用文件名，去掉量化尾巴）
    文件名没有信息量（model / gguf …）时退回目录名，
    但跳过组织/上传者目录（meta / Qwen / ggml-org …）——
    那是 owner 不是模型名，拿它当 uid 只会让客户端更认错。
    """
    if not model_file:
        return ""
    parts = [p for p in model_file.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    stem = os.path.splitext(parts[-1])[0]
    candidates = [stem] + list(reversed(parts[:-1]))
    for cand in candidates:
        cleaned = _clean_component(cand)
        if not cleaned or cleaned in _NON_MODEL_COMPONENTS:
            continue
        return cleaned
    return ""


def derive_uid(category: str, model_file: str, max_len: int = 48) -> str:
    """派生一个注册表友好的实例 uid。

    目标：客户端归一化匹配时能命中「同一个模型」的注册表条目，
    而不是裸家族名（`qwen3` → 会被认成 `qwen3-14b`）。
    """
    base = _model_identity(model_file) or ""
    category = (category or "").strip().lower()
    tokens = CATEGORY_TOKENS.get(category, ())
    has_identity = bool(base) and any(t in base for t in tokens)
    # 类别没在名字里体现时补上类别前缀，避免 embedding 实例被当成聊天模型
    if base and not has_identity and category and category != "chat":
        base = f"{category}-{base}"
    if not base:
        base = category or "model"
    if len(base) > max_len:
        base = base[:max_len].rstrip("-._")
    return base


def derive_display_name(category: str, model_file: str, fallback: str = "") -> str:
    """派生人类可读的模型名（客户端列表里显示的名字）。

    与 uid 刻意不同：客户端只有在自己没有 name、或 name 恰好等于 id 时
    才会用注册表覆盖名称；名字和 id 不同时我们的名字优先。
    """
    if not model_file:
        return fallback
    parts = [p for p in model_file.replace("\\", "/").split("/") if p]
    stem = os.path.splitext(parts[-1])[0]
    name = stem
    if not name or _clean_component(name) in _NON_MODEL_COMPONENTS:
        # 文件名没信息量时才退回目录，且跳过组织目录
        for cand in reversed(parts[:-1]):
            if _clean_component(cand) not in _NON_MODEL_COMPONENTS:
                name = cand
                break
        else:
            name = stem
    prev = None
    while prev != name:
        prev = name
        name = _TAIL_NOISE.sub("", name)
    name = name.strip(" -._")
    if not name:
        return fallback or stem
    label = (category or "").strip().lower()
    if label and label != "chat" and label not in name.lower():
        # 让不支持 capabilities 的客户端也能一眼看出用途
        name = f"{name} ({label})"
    return name


def assess_model_id(model_id: str, category: str) -> List[str]:
    """评估 uid 在客户端被认错的风险，返回给用户看的提示（可为空）。"""
    warnings: List[str] = []
    uid = (model_id or "").strip()
    category = (category or "").strip().lower()
    if not uid:
        return warnings

    tokens = CATEGORY_TOKENS.get(category, ())
    has_identity = any(t in uid.lower() for t in tokens)
    bare_family = bool(re.fullmatch(r"[a-z]+\d*(?:\.\d+)?", uid.lower()))

    if category != "chat" and not has_identity:
        warnings.append(
            f"实例 UID「{uid}」里没有 {category} 身份关键词，"
            f"客户端（如 Cherry Studio）会按模型名注册表模糊匹配，"
            f"可能被认成聊天模型并丢掉 {category} 能力。"
            f"建议改用带类别的 uid，例如 {derive_uid(category, '')}-…"
        )
    if bare_family:
        warnings.append(
            f"实例 UID「{uid}」是裸家族名，客户端归一化匹配会命中该家族的"
            f"具体尺寸条目（如 qwen3 → Qwen3 14B），名称与能力都会被覆盖。"
        )
    if re.search(r"[-_.]q\d", uid.lower()) or re.search(r"[-_.](?:fp16|bf16|fp8|gguf)$", uid.lower()):
        warnings.append(
            f"实例 UID「{uid}」带量化/格式后缀，客户端注册表匹配不到，"
            f"会被当成无能力的自定义模型。建议去掉量化后缀。"
        )
    return warnings


def instance_meta(inst) -> Dict[str, Any]:
    """把实例元数据整理成 /v1/models 需要的展示字段（name/description/capabilities）。"""
    category = (getattr(inst, "category", "") or "").lower()
    uid = getattr(inst, "model_id", "") or ""
    name = (getattr(inst, "name", "") or "").strip()
    if not name or name == uid:
        name = derive_display_name(category, getattr(inst, "selected_model_file", ""), fallback=uid)
    model_file = getattr(inst, "selected_model_file", "") or ""
    desc = f"{category or 'model'} 实例"
    if model_file:
        desc += f" · {os.path.basename(model_file)}"
    return {
        "name": name,
        "description": desc,
        "category": category,
        "capabilities": list(CATEGORY_CAPABILITIES.get(category, [])),
    }
