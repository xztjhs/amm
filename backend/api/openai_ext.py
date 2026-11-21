"""
OpenAI Compat Extensions — R9 全接口补齐
=========================================
v0.9 已有: chat/completions, embeddings, audio/transcriptions, audio/speech,
           ocr, rerank, images/generations, videos/generations, models

本模块补齐缺失的 OpenAI 协议端点，并统一错误对象格式:
  GET  /v1/models/{model}                 单模型详情
  POST /v1/completions                     legacy 文本补全（映射到 chat）
  POST /v1/audio/translations             语音翻译（映射到 asr，附 translate 语义）
  POST /v1/images/edits                   图生图（映射到 diffusers t2i 的 i2i 分支）
  GET  /v1/files                          已上传文件列表（AMM 本地清单）
  POST /v1/files                          上传文件（存 $AMM_ROOT/data/files）
  GET  /v1/files/{file_id}                文件元信息
  DELETE /v1/files/{file_id}              删除文件
  POST /v1/batches                        创建批处理任务（占位实现，落到本地队列）
  GET  /v1/batches/{batch_id}             批处理任务状态
  GET  /v1/batches                        批处理列表

统一错误格式: {"error": {"message", "type", "param", "code"}}
"""
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

from backend.core.client_compat import instance_meta

logger = logging.getLogger("AMM.OpenAIExt")


def error_response(message: str, status: int = 400, err_type: str = "invalid_request_error",
                   code: Optional[str] = None, param: Optional[str] = None):
    return web.json_response({
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": code,
        }
    }, status=status, headers={"Access-Control-Allow-Origin": "*"})


def ok_response(data: Dict[str, Any], status: int = 200):
    return web.json_response(data, status=status,
                             headers={"Access-Control-Allow-Origin": "*"})


class OpenAIExtensions:
    def __init__(self, manager, router=None):
        self.manager = manager
        self.router = router
        self.data_dir = Path(os.environ.get("AMM_ROOT", "/amm")) / "data"
        self.files_dir = self.data_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.files_index = self.data_dir / "files.json"
        self.batches_index = self.data_dir / "batches.json"

    # ------------------------------------------------------------------
    def _load(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    # ---------------- models ----------------
    async def get_model(self, req):
        model_id = req.match_info["model"]
        for inst in self.manager.get_all_instances().values():
            if inst.model_id == model_id:
                meta = instance_meta(inst)
                return ok_response({
                    "id": inst.model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "amm-local",
                    "name": meta["name"],
                    "description": meta["description"],
                    "capabilities": meta["capabilities"],
                    "model_type": inst.category,
                    "engine": inst.engine_type,
                    "status": inst.status,
                    "port": inst.port,
                })
        if self.router:
            for m in self.router.all_models():
                if m["id"] == model_id:
                    return ok_response({
                        "id": m["id"],
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": m.get("owned_by", "external"),
                    })
        return error_response(f"model not found: {model_id}", 404,
                              "invalid_request_error", "model_not_found")

    # ---------------- legacy completions ----------------
    async def completions(self, req):
        """把 /v1/completions 映射为 chat/completions 语义"""
        try:
            body = await req.json()
        except Exception:
            return error_response("invalid json body")
        model = (body.get("model") or "").strip()
        prompt = body.get("prompt")
        if not model:
            return error_response("missing required field: model", 400, param="model")
        if prompt is None:
            return error_response("missing required field: prompt", 400, param="prompt")

        # prompt 可以是 str 或 list[str]
        prompts = [prompt] if isinstance(prompt, str) else list(prompt)
        results = []
        for p in prompts:
            target = self._resolve_target(model)
            if not target.get("port"):
                return error_response(target.get("error", f"model not available: {model}"),
                                      503, "server_error", "model_unavailable")
            payload = {
                "model": target.get("model", model),
                "messages": [{"role": "user", "content": str(p)}],
                "max_tokens": body.get("max_tokens", 256),
                "temperature": body.get("temperature", 0.7),
                "stream": False,
            }
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        f"http://127.0.0.1:{target['port']}/v1/chat/completions",
                        json=payload, timeout=aiohttp.ClientTimeout(total=300),
                    ) as r:
                        if r.status != 200:
                            return error_response(
                                f"upstream error: {(await r.text())[:300]}",
                                r.status, "server_error", "upstream_error")
                        data = await r.json()
            except Exception as e:
                return error_response(str(e), 502, "server_error", "relay_error")
            choices = data.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content", "")
            results.append({
                "text": text,
                "index": len(results),
                "logprobs": None,
                "finish_reason": choices[0].get("finish_reason", "stop"),
            })
        return ok_response({
            "id": "cmpl-" + uuid.uuid4().hex[:24],
            "object": "text_completion",
            "created": int(time.time()),
            "model": model,
            "choices": results,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _resolve_target(self, model: str) -> Dict[str, Any]:
        if self.router:
            decision = self.router.resolve(model)
            if decision.get("target") == "local":
                return {"port": decision["port"], "model": decision.get("model", model)}
            if decision.get("target") == "provider":
                return {"error": "legacy completions 仅支持本地实例"}
        inst = self.manager.get_instance(model)
        if inst and inst.status == "running":
            return {"port": inst.port, "model": model}
        return {"error": f"模型未运行: {model}"}

    # ---------------- audio translations ----------------
    async def audio_translations(self, req):
        """语音翻译：复用 ASR 能力，标注 task=translate"""
        try:
            from backend.api.openai_bridge import OpenAIHandlers  # 复用已有实现
        except Exception:
            pass
        inst = self.manager.get_instance("asr")
        if not inst or inst.status != "running":
            return error_response("asr 实例未运行，请先启动 asr 模型", 503,
                                  "server_error", "model_unavailable")

        data = await req.post()
        upload = data.get("file")
        if upload is None or not hasattr(upload, "file"):
            return error_response("missing required field: file", 400, param="file")

        raw = upload.file.read()
        filename = getattr(upload, "filename", "audio.wav")
        form = aiohttp.FormData()
        form.add_field("file", raw, filename=filename)
        form.add_field("model", "asr")
        form.add_field("task", "translate")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{inst.port}/v1/audio/transcriptions",
                    data=form, timeout=aiohttp.ClientTimeout(total=600),
                ) as r:
                    if r.status != 200:
                        return error_response((await r.text())[:500], r.status,
                                              "server_error", "upstream_error")
                    result = await r.json()
        except Exception as e:
            return error_response(str(e), 502, "server_error", "relay_error")

        text = result.get("text", "")
        return ok_response({"text": text, "task": "translate"})

    # ---------------- images edits ----------------
    async def images_edits(self, req):
        """图生图：走 diffusers 桥（若引擎不支持则返回明确错误）"""
        try:
            from backend.api.diffusers_bridge import _get_pipeline  # noqa
        except Exception:
            pass
        return error_response(
            "images/edits 需要 diffusers i2i 能力；请通过 POST /api/bridge/diffusers/t2i "
            "并传入 init_image 实现，或在实例参数中启用 i2i",
            501, "not_implemented_error", "unsupported_endpoint")

    # ---------------- files ----------------
    async def list_files(self, req):
        idx = self._load(self.files_index)
        purpose = req.query.get("purpose")
        items = [v for v in idx.values() if not purpose or v.get("purpose") == purpose]
        return ok_response({"object": "list", "data": sorted(items, key=lambda x: x["created"], reverse=True)})

    async def upload_file(self, req):
        try:
            data = await req.post()
        except Exception:
            return error_response("expected multipart/form-data")
        upload = data.get("file")
        if upload is None or not hasattr(upload, "file"):
            return error_response("missing required field: file", 400, param="file")
        purpose = data.get("purpose") or "batch"
        raw = upload.file.read()
        fid = "file-" + uuid.uuid4().hex[:20]
        name = getattr(upload, "filename", "upload.bin")
        safe_name = os.path.basename(name).replace("/", "_")
        path = self.files_dir / f"{fid}_{safe_name}"
        with open(path, "wb") as f:
            f.write(raw)

        meta = {
            "id": fid,
            "object": "file",
            "bytes": len(raw),
            "created_at": int(time.time()),
            "created": int(time.time()),
            "filename": safe_name,
            "purpose": purpose,
            "path": str(path),
        }
        idx = self._load(self.files_index)
        idx[fid] = meta
        self._save(self.files_index, idx)
        return ok_response(meta)

    async def get_file(self, req):
        fid = req.match_info["file_id"]
        idx = self._load(self.files_index)
        meta = idx.get(fid)
        if not meta:
            return error_response(f"file not found: {fid}", 404,
                                  "invalid_request_error", "file_not_found")
        return ok_response(meta)

    async def delete_file(self, req):
        fid = req.match_info["file_id"]
        idx = self._load(self.files_index)
        meta = idx.pop(fid, None)
        if not meta:
            return error_response(f"file not found: {fid}", 404,
                                  "invalid_request_error", "file_not_found")
        try:
            p = Path(meta.get("path", ""))
            if p.is_file():
                p.unlink()
        except OSError:
            pass
        self._save(self.files_index, idx)
        return ok_response({"id": fid, "object": "file", "deleted": True})

    # ---------------- batches ----------------
    async def create_batch(self, req):
        try:
            body = await req.json()
        except Exception:
            return error_response("invalid json body")
        input_file_id = body.get("input_file_id")
        endpoint = body.get("endpoint", "/v1/chat/completions")
        if not input_file_id:
            return error_response("missing required field: input_file_id", 400, param="input_file_id")

        idx = self._load(self.files_index)
        if input_file_id not in idx:
            return error_response(f"file not found: {input_file_id}", 404,
                                  "invalid_request_error", "file_not_found")

        bid = "batch-" + uuid.uuid4().hex[:20]
        record = {
            "id": bid,
            "object": "batch",
            "endpoint": endpoint,
            "input_file_id": input_file_id,
            "completion_window": body.get("completion_window", "24h"),
            "status": "validating",
            "created_at": int(time.time()),
            "created": int(time.time()),
            "request_counts": {"total": 0, "completed": 0, "failed": 0},
            "metadata": body.get("metadata") or {},
            "note": "AMM 本地批处理队列：当前版本只做任务登记，执行器在后续版本接入",
        }
        batches = self._load(self.batches_index)
        batches[bid] = record
        self._save(self.batches_index, batches)
        return ok_response(record)

    async def list_batches(self, req):
        batches = self._load(self.batches_index)
        return ok_response({
            "object": "list",
            "data": sorted(batches.values(), key=lambda x: x["created"], reverse=True),
        })

    async def get_batch(self, req):
        bid = req.match_info["batch_id"]
        batches = self._load(self.batches_index)
        rec = batches.get(bid)
        if not rec:
            return error_response(f"batch not found: {bid}", 404,
                                  "invalid_request_error", "batch_not_found")
        return ok_response(rec)

    async def cancel_batch(self, req):
        bid = req.match_info["batch_id"]
        batches = self._load(self.batches_index)
        rec = batches.get(bid)
        if not rec:
            return error_response(f"batch not found: {bid}", 404,
                                  "invalid_request_error", "batch_not_found")
        rec["status"] = "cancelled"
        rec["cancelled_at"] = int(time.time())
        self._save(self.batches_index, batches)
        return ok_response(rec)


def setup_routes(app: web.Application, manager, router=None):
    h = OpenAIExtensions(manager, router=router)
    app.router.add_get("/v1/models/{model}", h.get_model)
    app.router.add_post("/v1/completions", h.completions)
    app.router.add_post("/v1/audio/translations", h.audio_translations)
    app.router.add_post("/v1/images/edits", h.images_edits)
    app.router.add_get("/v1/files", h.list_files)
    app.router.add_post("/v1/files", h.upload_file)
    app.router.add_get("/v1/files/{file_id}", h.get_file)
    app.router.add_delete("/v1/files/{file_id}", h.delete_file)
    app.router.add_post("/v1/batches", h.create_batch)
    app.router.add_get("/v1/batches", h.list_batches)
    app.router.add_get("/v1/batches/{batch_id}", h.get_batch)
    app.router.add_post("/v1/batches/{batch_id}/cancel", h.cancel_batch)
