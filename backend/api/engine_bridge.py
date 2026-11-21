"""
Engine Bridge (v1.1)
====================
引擎目录 / 安装任务 / 参数 schema / 模型文件候选。

  GET  /api/engines/catalog?refresh=1        引擎目录（上游版本 + 本地安装状态）
  GET  /api/engines/installed                仅本地已安装
  POST /api/engines/tasks                    {action,engine_type,version,flavor}
  GET  /api/engines/tasks                    任务列表（含进度/日志尾巴）
  GET  /api/engines/tasks/{task_id}          单任务
  GET  /api/schema/params?category=&engine=  参数定义（来自 models_config）
  GET  /api/schema/models?category=&engine=  可用模型文件候选（扫描 /models）
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from aiohttp import web

from backend.core import engine_catalog as catalog_mod

logger = logging.getLogger("AMM.EngineBridge")

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
GGUF_ENGINES = {"llama_cpp"}
HF_DIR_ENGINES = {"vllm", "diffusers", "sentence_transformers", "vllm_embedding"}


def _category_of_cfg(cfg: Dict[str, Any]) -> str:
    return (cfg.get("category") or "").strip().lower()


def setup_routes(app: web.Application, manager):
    """注册引擎目录路由（含 PyPI 镜像配置）"""

    async def get_pip_settings(req):
        from backend.core.engine_catalog import PIP_PRESETS, get_pip_index
        return web.json_response({
            "index_url": get_pip_index(),
            "presets": [{"id": k, **v} for k, v in PIP_PRESETS.items()],
        }, headers={"Access-Control-Allow-Origin": "*"})

    async def save_pip_settings(req):
        from backend.core.engine_catalog import set_pip_index, get_pip_index
        try:
            body = await req.json()
        except Exception:
            body = {}
        url = str((body or {}).get("index_url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            return web.json_response({"error": "镜像地址必须以 http(s):// 开头"},
                                     status=400)
        effective = set_pip_index(url)
        logger.info(f"PyPI 镜像已设为: {effective}")
        return web.json_response({"success": True, "index_url": effective,
                                  "effective": get_pip_index()},
                                 headers={"Access-Control-Allow-Origin": "*"})

    app.router.add_get("/api/settings/pip", get_pip_settings)
    app.router.add_post("/api/settings/pip", save_pip_settings)
    _setup_engine_routes(app, manager)


def _setup_engine_routes(app: web.Application, manager):
    class Handlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        # ---------------- 目录 ----------------
        async def get_catalog(self, req):
            refresh = req.query.get("refresh") in ("1", "true", "yes")
            try:
                data = await catalog_mod.catalog(refresh=refresh)
            except Exception as e:
                logger.error(f"catalog 失败: {e}")
                return self._json({"error": str(e)}, 500)
            return self._json(data)

        async def get_installed(self, req):
            out = {}
            for et in catalog_mod.ENGINE_META:
                meta = catalog_mod.ENGINE_META[et]
                out[et] = {
                    "display_name": meta["display_name"],
                    "supports": meta["supports"],
                    "flavors": meta["flavors"],
                    "default_flavor": meta["default_flavor"],
                    "kind": meta["kind"],
                    "note": meta["note"],
                    "upstream_source": meta["upstream"],
                    "installed": catalog_mod.installed_versions(et),
                }
            return self._json({
                "engines_root": catalog_mod.ENGINES_ROOT,
                "engines": out,
            })

        # ---------------- 任务 ----------------
        async def start_task(self, req):
            try:
                body = await req.json()
            except Exception as e:
                return self._json({"error": f"无效 JSON: {e}"}, 400)
            try:
                r = catalog_mod.start_task(
                    engine_type=(body.get("engine_type") or "").strip(),
                    version=(body.get("version") or "").strip(),
                    flavor=(body.get("flavor") or "").strip(),
                    action=(body.get("action") or "install").strip(),
                )
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(r)

        async def list_tasks(self, req):
            return self._json({"tasks": catalog_mod.list_tasks()})

        async def get_task(self, req):
            tid = req.match_info["task_id"]
            t = catalog_mod.get_task(tid)
            if not t:
                return self._json({"error": "任务不存在"}, 404)
            return self._json(t)

        # ---------------- 参数 schema ----------------
        async def get_params(self, req):
            category = (req.query.get("category") or "").strip().lower()
            engine = (req.query.get("engine") or "").strip()
            if not category:
                return self._json({"error": "缺少 category"}, 400)

            cfg = None
            for key in manager._model_keys():
                c = manager.config.get(key) or {}
                if _category_of_cfg(c) == category:
                    cfg = c
                    break
            if cfg is None:
                return self._json({"parameters": [], "source": "none"})

            params = []
            for p in cfg.get("parameters") or []:
                pe = (p.get("engine") or "").strip()
                if pe and engine and pe != engine:
                    continue
                # LLM 采样参数对非 chat 类别无意义，过滤掉减少噪音
                if category != "chat" and p.get("name") in (
                        "temp", "top_p", "top_k", "min_p", "repeat_penalty",
                        "repeat_last_n", "mirostat", "mirostat_lr", "mirostat_ent",
                        "frequency_penalty", "presence_penalty", "no_reasoning",
                        "reasoning_budget", "reasoning_budget_enabled",
                        "max_tokens", "stop"):
                    continue
                item = {k: v for k, v in p.items() if k != "engine"}
                item["engine"] = pe
                params.append(item)

            # 引擎专属补充项（未在 yaml 中声明的常用参数）
            names = {p["name"] for p in params}
            if engine == "llama_cpp":
                if category in ("ocr", "asr", "tts") and "mmproj" not in names:
                    params.append({"name": "mmproj", "label": "MMProj 文件 (视觉/音频投影)",
                                   "type": "string", "default": "", "engine": "llama_cpp"})
            if engine in ("vllm",) and "gpu_memory_utilization" not in names:
                params.append({"name": "gpu_memory_utilization", "label": "GPU 显存利用率",
                               "type": "float", "default": 0.9, "min": 0.1, "max": 1.0,
                               "step": 0.05, "engine": "vllm"})
            if engine == "sentence_transformers":
                params.extend([
                    {"name": "device", "label": "设备", "type": "select",
                     "options": ["auto", "cuda", "cpu"], "default": "auto"},
                    {"name": "batch_size", "label": "批大小", "type": "number", "default": 32, "min": 1, "max": 512},
                    {"name": "normalize", "label": "向量归一化", "type": "boolean", "default": True},
                    {"name": "max_seq_length", "label": "最大序列长度", "type": "number",
                     "default": 8192, "min": 128, "max": 32768},
                ])
            if engine == "diffusers":
                params.extend([
                    {"name": "num_inference_steps", "label": "默认推理步数", "type": "number",
                     "default": 28, "min": 4, "max": 100},
                    {"name": "guidance_scale", "label": "默认 CFG", "type": "float",
                     "default": 5.0, "min": 1.0, "max": 20.0, "step": 0.5},
                    {"name": "quant", "label": "量化", "type": "select",
                     "options": ["", "fp8", "gguf"], "default": ""},
                    {"name": "offload", "label": "显存策略", "type": "select",
                     "options": ["gpu", "model", "group"], "default": "gpu"},
                ])
            # 去重：yaml 声明 + 引擎补充项可能重名，保留先出现的（yaml 优先）
            seen = set()
            deduped = []
            for p in params:
                if p["name"] in seen:
                    continue
                seen.add(p["name"])
                deduped.append(p)
            return self._json({
                "category": category, "engine": engine,
                "source": cfg.get("id"), "parameters": deduped,
            })

        # ---------------- 模型文件候选 ----------------
        async def get_models(self, req):
            """扫描模型目录，返回可作为 selected_model_file 的候选。

            /models 下可能有几十万文件，这里限制深度与条目数，并在执行器里跑，
            避免阻塞 aiohttp 事件循环。
            """
            category = (req.query.get("category") or "").strip().lower()
            engine = (req.query.get("engine") or "").strip()
            cfg_root = manager.config.get("server", {}).get("models_dir") or ""
            candidates = [p for p in (cfg_root, MODELS_DIR) if p]
            root = next((Path(p) for p in candidates if Path(p).is_dir()), Path(candidates[0]))
            if not root.is_dir():
                return self._json({"files": [], "dirs": [], "error": f"{root} 不存在"})

            want_gguf = engine in GGUF_ENGINES
            MAX_DEPTH = 5
            MAX_FILES = 400
            files: List[Dict[str, Any]] = []
            dirs: List[Dict[str, Any]] = []

            def _scan():
                stack = [(root, 0)]
                while stack and len(files) < MAX_FILES:
                    cur, depth = stack.pop()
                    try:
                        entries = sorted(os.scandir(cur), key=lambda e: e.name.lower())
                    except Exception:
                        continue
                    subdirs = []
                    for e in entries:
                        if e.name.startswith("."):
                            continue
                        try:
                            rel = str(Path(e.path).relative_to(root))
                        except Exception:
                            continue
                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                            if is_dir:
                                if depth < MAX_DEPTH:
                                    subdirs.append((Path(e.path), depth + 1))
                                if (Path(e.path) / "config.json").is_file() or \
                                        (Path(e.path) / "model_index.json").is_file():
                                    dirs.append({"path": rel, "name": e.name,
                                                 "has_config": True, "size_mb": 0})
                                continue
                            if not e.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        suffix = Path(e.name).suffix.lower()
                        if want_gguf and suffix != ".gguf":
                            continue
                        if not want_gguf and suffix not in (".safetensors", ".bin", ".gguf"):
                            continue
                        try:
                            size_mb = round(e.stat().st_size / (1024 * 1024), 1)
                        except OSError:
                            size_mb = 0
                        files.append({
                            "path": rel, "name": e.name, "size_mb": size_mb,
                            "is_mmproj": "mmproj" in e.name.lower(),
                            "quant": _guess_quant(e.name),
                        })
                        if len(files) >= MAX_FILES:
                            break
                    stack.extend(reversed(subdirs))

            import asyncio
            await asyncio.get_event_loop().run_in_executor(None, _scan)

            files.sort(key=lambda f: f["path"].lower())
            dirs.sort(key=lambda d: d["path"].lower())
            return self._json({
                "root": str(root), "engine": engine, "category": category,
                "files": files[:300], "dirs": dirs[:120],
                "truncated": len(files) >= MAX_FILES,
            })

    h = Handlers()
    app.router.add_get("/api/engines/catalog", h.get_catalog)
    app.router.add_get("/api/engines/installed", h.get_installed)
    app.router.add_post("/api/engines/tasks", h.start_task)
    app.router.add_get("/api/engines/tasks", h.list_tasks)
    app.router.add_get("/api/engines/tasks/{task_id}", h.get_task)
    app.router.add_get("/api/schema/params", h.get_params)
    app.router.add_get("/api/schema/models", h.get_models)


def _dir_size_mb(p: Path) -> float:
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return round(total / (1024 * 1024), 1)


def _guess_quant(name: str) -> str:
    low = name.lower()
    for q in ("q2_k", "q3_k_m", "q3_k_s", "q4_k_m", "q4_k_s", "q4_0", "q4_1",
              "q5_k_m", "q5_k_s", "q5_0", "q6_k", "q8_0", "iq4_xs", "f16", "bf16", "f32"):
        if q in low:
            return q.upper()
    return ""
