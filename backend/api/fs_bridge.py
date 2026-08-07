#!/usr/bin/env python3
"""
AMM - 模型文件浏览 / 预设参数加载 API
=====================================
支撑 v0.2「先选引擎 → 浏览路径选模型文件 → 识别可调参数 → 加载 .vllm/.llamacpp 预设」流程。

约定
----
- 所有路径均在 models_dir 约束范围内（防目录穿越）。
- 模型文件旁可放置同名预设配置：
    <model_file>.vllm        (YAML/JSON，key=参数名 value=参数值)
    <model_file>.llamacpp    (YAML/JSON)
    例: /models/xx/Qwen.gguf.llamacpp 或 /models/xx/model.vllm
- 预设文件命名规则: <模型文件全名>.<engine> 或 <不带扩展名的模型名>.<engine>
"""
import os
from pathlib import Path
import json
import logging
from typing import Dict, List, Optional

import yaml
from aiohttp import web

logger = logging.getLogger("AMM.FS")

# 各类模型在文件系统里常见的模型文件扩展名
MODEL_FILE_EXTS = {
    "gguf": [".gguf", ".ggufv2", ".safetensors", ".bin"],
    "ggml": [".gguf", ".bin", ".ggml"],
    "safetensors": [".safetensors", ".bin"],
    "torch": [".pth", ".pt"],
    "any": [".gguf", ".safetensors", ".bin", ".pth", ".pt"],
}
# 目录内优先被识别为「模型文件」的特征（宽松匹配，命中任一即视为模型文件所在）
HINT_FILES = ["config.json", "model.safetensors", "pytorch_model.bin", "model_index.json"]


def _normalize_root(models_dir: str) -> Path:
    root = Path(models_dir).resolve() if models_dir else Path("/models")
    return root


class FSBridge:
    def __init__(self, manager):
        self.manager = manager
        self.models_dir = os.environ.get("MODELS_DIR", "/models")

    # ---------------------------------------------------------
    # 列表
    # ---------------------------------------------------------
    def list_models(self, path: str = "") -> web.Response:
        root = _normalize_root(self.models_dir)
        target = (root / path).resolve() if path else root
        # 防穿越: 必须在 root 之下
        try:
            target.relative_to(root)
        except ValueError:
            return self._json({"error": "路径越界"}, 400)

        if not target.exists():
            return self._json({"error": "目录不存在"}, 404)
        if not target.is_dir():
            return self._json({"error": "不是目录"}, 400)

        dirs, files = [], []
        for entry in sorted(target.iterdir(), key=lambda x: (-x.is_dir(), x.name.lower())):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(root)),
                    "type": "dir",
                })
            else:
                files.append(self._file_info(entry, root))

        return self._json({
            "root": str(root),
            "current": str(target),
            "relative": str(target.relative_to(root)) if target != root else "",
            # 上级目录: 回到根时返回空字符串(表示根), 根目录本身无上级(返回 None)
            "parent_relative": (str(target.parent.relative_to(root)) if target.parent != root else "") if target != root else None,
            "dirs": dirs,
            "files": files,
        })

    def _file_info(self, entry: Path, root: Path) -> Dict:
        size = 0
        try:
            size = entry.stat().st_size
        except Exception:
            pass
        ext = entry.suffix.lower()
        return {
            "name": entry.name,
            "path": str(entry.relative_to(root)),
            "type": "file",
            "ext": ext,
            "size_mb": round(size / (1024 * 1024), 1) if size else 0,
        }

    # ---------------------------------------------------------
    # 模型文件识别 (在给定目录里挑出"最像模型"的文件)
    # ---------------------------------------------------------
    def discover(self, dir_path: str = "", engine: str = "") -> web.Response:
        root = _normalize_root(self.models_dir)
        target = (root / dir_path).resolve() if dir_path else root
        try:
            target.relative_to(root)
        except ValueError:
            return self._json({"error": "路径越界"}, 400)
        if not target.is_dir():
            return self._json({"error": "目录不存在"}, 404)

        exts = set()
        if engine == "llama_cpp" or engine == "llama":
            exts = {".gguf", ".bin", ".ggml"}
        elif engine == "vllm":
            exts = {".safetensors", ".bin", ".gguf"}
        elif engine == "diffusers":
            exts = set()  # 目录型模型，用 config.json 识别

        candidates = []
        for entry in sorted(target.iterdir()):
            if entry.is_file() and entry.suffix.lower() in exts:
                candidates.append(self._file_info(entry, root))

        is_hf_dir = (target / "config.json").exists() or (target / "model.safetensors").exists() or (target / "pytorch_model.bin").exists()
        result = {
            "dir": str(target.relative_to(root)) if target != root else "",
            "engine": engine,
            "model_files": candidates,
            "is_huggingface_dir": is_hf_dir,
            "model_path_hint": (str(target.relative_to(root)) if target != root else ""),
        }
        return self._json(result)

    # ---------------------------------------------------------
    # 预设配置 读取
    # ---------------------------------------------------------
    def find_preset(self, model_file: str, engine: str) -> Optional[Dict]:
        """按模型文件路径 + 引擎，查找同名 .vllm/.llamacpp 预设配置文件并解析。

        支持三种命名：
          1) <model_file>.llamacpp      (紧跟文件名)
          2) <model_file_stem>.llamacpp (模型名不带后缀)
          3) <dir>/<dir_basename>.llamacpp (目录内与目录同名)
        """
        root = _normalize_root(self.models_dir)
        if not model_file:
            return None
        p = (root / model_file).resolve()
        if not p.exists():
            return None

        ext_map = {"vllm": ".vllm", "llama_cpp": ".llamacpp", "llama": ".llamacpp"}
        preset_ext = ext_map.get(engine)
        if not preset_ext:
            return None

        candidates = [
            Path(str(p) + preset_ext),                     # a.gguf.llamacpp
            p.with_suffix("").with_suffix(preset_ext),     # a.llamacpp
            p.parent / (p.parent.name + preset_ext),       # <dir>/<dirname>.llamacpp
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                try:
                    data = self._parse_preset(c)
                    if data is not None:
                        return {"path": str(c.relative_to(root)), "data": data}
                except Exception as e:
                    logger.warning(f"预设解析失败 {c}: {e}")
        return None

    def _parse_preset(self, path: Path) -> Optional[Dict]:
        text = path.read_text(encoding="utf-8", errors="replace")
        text = text.strip()
        if not text:
            return {}
        # YAML 通常能解析 JSON；JSON 也能被 yaml 解析
        try:
            data = yaml.safe_load(text)
        except Exception:
            # 兜底: 简单 key=value 行 (支持 # 注释)
            data = {}
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if not k:
                    continue
                try:
                    v = json.loads(v)  # 尝试数值/布尔
                except Exception:
                    pass
                data[k] = v
        return data if isinstance(data, dict) else {"_preset": data}

    async def get_preset(self, req) -> web.Response:
        q = req.query
        model_file = q.get("model_file", "")
        engine = q.get("engine", "")
        preset = self.find_preset(model_file, engine)
        if preset is None:
            return self._json({"found": False, "model_file": model_file, "engine": engine})
        return self._json({"found": True, **preset})

    # ---------------------------------------------------------
    # 应用预设 → instance.parameters
    # ---------------------------------------------------------
    async def apply_preset(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        model_id = body.get("model_id", "")
        model_file = body.get("model_file", "")
        engine = body.get("engine", "")
        if not model_id:
            return self._json({"error": "缺少 model_id"}, 400)

        preset = self.find_preset(model_file, engine)
        if preset is None:
            return self._json({"ok": False, "error": "未找到预设配置文件"})

        inst = self.manager.get_instance(model_id)
        if inst is None:
            return self._json({"error": "模型不存在"}, 404)

        # 合并预设到 parameters
        merged = dict(preset["data"])
        inst.parameters.update(merged)
        self.manager._save_state()
        logger.info(f"模型 {model_id} 应用预设 {preset['path']}: {params_keys(merged)}")
        return self._json({
            "ok": True,
            "preset_path": preset["path"],
            "applied": params_keys(merged),
            "parameters": params_preview(inst.parameters),
        })

    # ---------------------------------------------------------
    # 保存预设
    # ---------------------------------------------------------
    async def save_preset(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        model_file = body.get("model_file", "")
        engine = body.get("engine", "")
        params = body.get("parameters", {})
        if not model_file or not engine:
            return self._json({"error": "缺少 model_file/engine"}, 400)

        root = _normalize_root(self.models_dir)
        p = (root / model_file).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            return self._json({"error": "路径越界"}, 400)
        if not p.parent.is_dir():
            return self._json({"error": "父目录不存在"}, 404)

        ext_map = {"vllm": ".vllm", "llama_cpp": ".llamacpp"}
        preset_ext = ext_map.get(engine)
        if not preset_ext:
            return self._json({"error": "不支持的引擎"}, 400)

        preset_path = Path(str(p) + preset_ext)
        try:
            preset_path.write_text(
                yaml.safe_dump(params, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as e:
            return self._json({"error": f"写入失败: {e}"}, 500)
        return self._json({"ok": True, "path": str(preset_path.relative_to(root))})

    # ---- helpers ----
    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                 headers={"Access-Control-Allow-Origin": "*"})

    def _jsonify(self, data):
        return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})


def params_keys(d: Dict) -> List[str]:
    """返回 d 的键名（用于日志/响应，避免超长）"""
    return sorted(d.keys())


def params_preview(d: Dict, limit: int = 40) -> Dict:
    return {k: v for k, v in list(d.items())[:limit]}


async def setup_route_mkdir(bridge, req):
    """POST /api/fs/mkdir - 在 /models 下新建目录(v0.5 输出目录新建用) """
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "无效 JSON"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    rel = (body.get("path") or "").strip().lstrip("/")
    root = _normalize_root(os.environ.get("MODELS_DIR", "/models"))
    tgt = (root / rel).resolve() if rel else root
    try:
        tgt.relative_to(root)
    except ValueError:
        return web.json_response({"error": "路径越界"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    try:
        tgt.mkdir(parents=True, exist_ok=True)
        return web.json_response({"ok": True, "path": str(tgt.relative_to(root)) if tgt != root else ""},
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


def setup_routes(app: web.Application, manager):
    """注册文件浏览 / 预设配置 API 路由"""
    bridge = FSBridge(manager)

    def _m(h):
        return h

    app.router.add_get("/api/fs/list", _m(lambda req: bridge.list_models(req.query.get("path", ""))))
    app.router.add_get("/api/fs/discover", _m(lambda req: bridge.discover(req.query.get("dir", ""), req.query.get("engine", ""))))
    app.router.add_post("/api/fs/mkdir", _m(lambda req: set_route_mkdir(req)))
    app.router.add_get("/api/instances/preset", _m(lambda req, bridge=bridge: bridge.get_preset(req)))
    app.router.add_post("/api/instances/preset/apply", _m(lambda req, bridge=bridge: bridge.apply_preset(req)))
    app.router.add_post("/api/instances/preset/save", _m(lambda req, bridge=bridge: bridge.save_preset(req)))