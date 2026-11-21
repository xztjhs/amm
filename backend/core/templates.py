"""
Model Templates — 模型模版系统（R2）
====================================
参考 Xinference launch-model：把「下载 -> 配置 -> 部署」固化成可复用模版。

模版就是一个 YAML 文件，描述:
  - 模型卡信息（名称/类型/描述/标签）
  - 下载源（HuggingFace / ModelScope 的 repo_id 与文件）
  - 推荐引擎与版本约束、各 flavor 的默认参数
  - 量化选项与显存估算

目录约定（固定路径，不改动现有结构）:
  $AMM_ROOT/templates/<category>/<name>.yaml     内置/用户模版
  $AMM_ROOT/templates/custom/<name>.yaml         从实例「另存为模版」

任何模版解析失败只记录告警，不影响其它模版与主服务。
"""
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("AMM.ModelTemplates")

_CATEGORY_ALIASES = {
    "llm": "chat", "chat": "chat", "vlm": "chat",
    "embedding": "embedding", "embed": "embedding",
    "rerank": "reranker", "reranker": "reranker",
    "asr": "asr", "tts": "tts", "ocr": "ocr",
    "image": "image", "t2i": "image",
    "video": "video", "t2v": "video", "i2v": "video",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_category(value: str) -> str:
    return _CATEGORY_ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())


class TemplateStore:
    """模版库：加载/校验/保存/删除"""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _iter_files(self):
        for p in sorted(self.root.rglob("*.yaml")):
            if any(part.startswith(".") for part in p.parts):
                continue
            yield p
        for p in sorted(self.root.rglob("*.yml")):
            if any(part.startswith(".") for part in p.parts):
                continue
            yield p

    def _parse(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"模版解析失败 {path}: {e}")
            return None
        if not isinstance(data, dict):
            logger.warning(f"模版格式非法（非对象）: {path}")
            return None

        name = str(data.get("name") or data.get("model_name") or path.stem).strip()
        category = normalize_category(data.get("category") or data.get("model_type") or "")
        if not category:
            logger.warning(f"模版缺少 category: {path}")
            return None

        engines = data.get("engines") or []
        if isinstance(engines, (str, dict)):
            engines = [engines]
        norm_engines = []
        for e in engines:
            if isinstance(e, str):
                norm_engines.append({"engine": e, "params": {}})
            elif isinstance(e, dict):
                norm_engines.append({
                    "engine": e.get("engine") or e.get("engine_type") or "",
                    "version": e.get("version") or "",
                    "flavor": e.get("flavor") or "",
                    "params": e.get("params") or e.get("parameters") or {},
                })

        sources = data.get("sources") or []
        if isinstance(sources, dict):
            sources = [sources]
        norm_sources = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            norm_sources.append({
                "provider": (s.get("provider") or s.get("source") or "").lower(),
                "repo_id": s.get("repo_id") or s.get("model_id") or "",
                "files": s.get("files") or [],
                "revision": s.get("revision") or "",
            })

        return {
            "id": data.get("id") or path.stem,
            "name": name,
            "category": category,
            "description": data.get("description") or "",
            "tags": data.get("tags") or [],
            "sources": norm_sources,
            "engines": norm_engines,
            "quantization": data.get("quantization") or [],
            "model_file": data.get("model_file") or "",
            "parameters": data.get("parameters") or [],
            "vram_hint_gb": data.get("vram_hint_gb"),
            "context_length": data.get("context_length"),
            "path": str(path),
            "is_custom": "custom" in path.parts,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    def list(self, category: str = "", keyword: str = "") -> List[Dict[str, Any]]:
        want_cat = normalize_category(category) if category else ""
        kw = (keyword or "").strip().lower()
        out = []
        for path in self._iter_files():
            tpl = self._parse(path)
            if not tpl:
                continue
            if want_cat and tpl["category"] != want_cat:
                continue
            if kw:
                hay = " ".join([
                    str(tpl["name"]), str(tpl["id"]), str(tpl["description"]),
                    " ".join(map(str, tpl["tags"])),
                ]).lower()
                if kw not in hay:
                    continue
            out.append(tpl)
        return out

    def get(self, template_id: str) -> Optional[Dict[str, Any]]:
        for path in self._iter_files():
            if path.stem == template_id:
                return self._parse(path)
        return None

    def save(self, payload: Dict[str, Any], custom: bool = True) -> Dict[str, Any]:
        """保存模版（默认写入 custom/ 目录）"""
        name = str(payload.get("name") or payload.get("model_name") or "").strip()
        category = normalize_category(payload.get("category") or payload.get("model_type") or "")
        if not name:
            raise ValueError("模版缺少 name")
        if not category:
            raise ValueError("模版缺少 category")

        tpl_id = str(payload.get("id") or "").strip()
        if not tpl_id:
            tpl_id = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower() or "template"
        if not _SAFE_NAME.match(tpl_id):
            raise ValueError(f"模版 id 非法: {tpl_id}")

        subdir = self.root / "custom" if custom else self.root / category
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{tpl_id}.yaml"

        data = dict(payload)
        data["id"] = tpl_id
        data["name"] = name
        data["category"] = category
        # 去掉空值字段，避免 yaml 里出现一堆 `key: null`
        for key in ("context_length", "vram_hint_gb", "model_file", "description"):
            if data.get(key) in (None, ""):
                data.pop(key, None)
        for key in ("tags", "sources", "engines", "quantization", "parameters"):
            if data.get(key) in (None, [], {}):
                data.pop(key, None)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

        logger.info(f"模版已保存: {path}")
        return self._parse(path) or {"id": tpl_id, "path": str(path)}

    def delete(self, template_id: str) -> bool:
        """只允许删除 custom/ 下的模版"""
        for path in self._iter_files():
            if path.stem != template_id:
                continue
            if "custom" not in path.parts:
                raise PermissionError("内置模版不可删除，仅 custom/ 目录可删")
            path.unlink()
            logger.info(f"模版已删除: {path}")
            return True
        return False

    def update(self, template_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """编辑模版。

        规则:
          - custom/ 下模版: 原地更新（允许改 id -> 改名文件）
          - 内置模版: 复制为 custom/<id>-custom.yaml，不动内置文件
        """
        src = None
        for path in self._iter_files():
            if path.stem == template_id:
                src = path
                break
        if src is None:
            raise ValueError(f"模版不存在: {template_id}")

        data: Dict[str, Any] = {}
        try:
            with open(src, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"模版读取失败: {e}")
        if not isinstance(data, dict):
            data = {}

        # 只覆盖调用方给出的字段，其余保留
        for key in ("name", "category", "description", "tags", "sources",
                    "engines", "quantization", "vram_hint_gb", "context_length",
                    "model_file", "parameters"):
            if key in payload and payload[key] is not None:
                data[key] = payload[key]
        data["id"] = payload.get("id") or template_id

        is_custom = "custom" in src.parts
        if not is_custom:
            data["id"] = f"{data['id']}-custom"
            logger.info(f"内置模版编辑 -> 另存为自定义副本: {data['id']}")

        new_path = src if is_custom else (self.root / "custom" / f"{data['id']}.yaml")
        saved = self.save(data, custom=is_custom)
        if is_custom and saved.get("path") and Path(saved["path"]).name != src.name:
            # id 变化 -> 删除旧文件
            try:
                src.unlink()
            except Exception:
                pass
        return saved

    def duplicate(self, template_id: str, new_id: str = "", new_name: str = "") -> Dict[str, Any]:
        """复制模版为新的自定义模版"""
        tpl = self.get(template_id)
        if not tpl:
            raise ValueError(f"模版不存在: {template_id}")
        data = {
            "id": new_id or f"{template_id}-copy",
            "name": new_name or f"{tpl['name']} (副本)",
            "category": tpl["category"],
            "description": tpl["description"],
            "tags": list(tpl["tags"]),
            "sources": tpl["sources"],
            "engines": tpl["engines"],
            "quantization": tpl["quantization"],
            "model_file": tpl.get("model_file", ""),
            "parameters": tpl.get("parameters", []),
            "vram_hint_gb": tpl.get("vram_hint_gb"),
            "context_length": tpl.get("context_length"),
        }
        return self.save(data, custom=True)

    def from_instance(self, manager, model_id: str) -> Dict[str, Any]:
        """把运行实例「另存为模版」"""
        inst = manager.instances.get(model_id)
        if not inst:
            raise ValueError(f"实例不存在: {model_id}")
        return {
            "id": model_id,
            "name": inst.name or model_id,
            "category": inst.category,
            "description": f"由实例 {model_id} 导出的模版",
            "tags": ["exported", "dynamic" if inst.is_dynamic else "legacy"],
            "sources": [],
            "engines": [{
                "engine": inst.engine_type,
                "version": inst.engine_version,
                "flavor": inst.flavor,
                "params": dict(inst.parameters or {}),
            }],
            "model_file": inst.selected_model_file,
            "parameters": [],
            "quantization": [],
        }
