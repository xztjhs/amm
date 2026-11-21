#!/usr/bin/env python3
"""
sentence-transformers 子服务
============================
由 SentenceTransformersEngine 拉起，对 AMM 主服务暴露统一的 HTTP 契约：

  GET  /health
  GET  /v1/models
  POST /v1/embeddings   {"input": "text" | ["a","b"], "model": "..."}
  POST /v1/rerank       {"query": "...", "documents": ["a","b"], "top_n": 3}

必须在该 flavor 的 venv 里运行（torch cpu / cu130 各自版本）。
"""
import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("STServer")

_model = None
_model_path = ""
_category = "embedding"
_normalize = True
_batch_size = 32
_device = "cpu"
_max_seq_length = 0


def load_model():
    global _model
    from sentence_transformers import SentenceTransformer, CrossEncoder
    logger.info(f"加载模型: {_model_path} (device={_device}, category={_category})")
    t0 = time.time()
    if _category == "reranker":
        _model = CrossEncoder(_model_path, device=_device)
    else:
        kwargs = {"device": _device}
        if _max_seq_length:
            kwargs["max_seq_length"] = _max_seq_length
        _model = SentenceTransformer(_model_path, **kwargs)
    logger.info(f"模型加载完成 {time.time() - t0:.1f}s")


def _json_response(data: Dict[str, Any], status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status)


async def handle_health(request):
    return _json_response({"status": "ok", "model": _model_path, "category": _category, "device": _device})


async def handle_models(request):
    name = os.path.basename(_model_path.rstrip("/")) or "st-model"
    return _json_response({"object": "list", "data": [
        {"id": name, "object": "model", "created": int(time.time()), "owned_by": "amm-st"}
    ]})


async def handle_embeddings(request):
    from aiohttp import web
    try:
        body = await request.json()
    except Exception as e:
        return _json_response({"error": {"message": f"invalid json: {e}", "type": "invalid_request_error"}}, 400)

    inputs = body.get("input")
    if inputs is None:
        return _json_response({"error": {"message": "missing input", "type": "invalid_request_error"}}, 400)
    if isinstance(inputs, str):
        texts = [inputs]
    elif isinstance(inputs, list) and all(isinstance(x, str) for x in inputs):
        texts = inputs
    else:
        return _json_response({"error": {"message": "input must be string or list[str]", "type": "invalid_request_error"}}, 400)

    t0 = time.time()
    try:
        vectors = _model.encode(
            texts,
            batch_size=_batch_size,
            normalize_embeddings=_normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    except Exception as e:
        logger.error(f"embedding 失败: {e}")
        return _json_response({"error": {"message": str(e), "type": "server_error"}}, 500)

    data = []
    total_tokens = 0
    for i, vec in enumerate(vectors):
        data.append({"object": "embedding", "index": i, "embedding": [float(x) for x in vec]})
        total_tokens += max(1, len(texts[i]) // 4)  # 粗估

    return _json_response({
        "object": "list",
        "data": data,
        "model": os.path.basename(_model_path.rstrip("/")) or "st-model",
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    })


async def handle_rerank(request):
    try:
        body = await request.json()
    except Exception as e:
        return _json_response({"error": f"invalid json: {e}"}, 400)

    query = body.get("query") or body.get("q")
    documents = body.get("documents") or body.get("docs") or []
    top_n = body.get("top_n") or len(documents)

    if not query or not documents:
        return _json_response({"error": "query and documents are required"}, 400)

    t0 = time.time()
    try:
        pairs = [[query, str(d)] for d in documents]
        scores = _model.predict(pairs, batch_size=_batch_size, show_progress_bar=False)
    except Exception as e:
        logger.error(f"rerank 失败: {e}")
        return _json_response({"error": str(e)}, 500)

    results = [
        {"index": i, "relevance_score": float(s), "document": {"text": str(documents[i])}}
        for i, s in enumerate(scores)
    ]
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    results = results[:top_n]

    return _json_response({
        "model": os.path.basename(_model_path.rstrip("/")) or "st-model",
        "results": results,
        "usage": {"total_tokens": max(1, len(query) // 4) + sum(max(1, len(str(d)) // 4) for d in documents)},
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    })


def main():
    global _model_path, _category, _normalize, _batch_size, _device, _max_seq_length
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--category", default="embedding", choices=["embedding", "reranker"])
    ap.add_argument("--device", default="")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--no-normalize", action="store_true")
    ap.add_argument("--max-seq-length", type=int, default=0)
    ap.add_argument("--trust-remote-code", action="store_true")
    args = ap.parse_args()

    _model_path = args.model
    _category = args.category
    _normalize = not args.no_normalize
    _batch_size = args.batch_size
    _max_seq_length = args.max_seq_length
    _device = args.device or "cpu"

    from aiohttp import web
    load_model()

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/embeddings", handle_embeddings)
    app.router.add_post("/v1/rerank", handle_rerank)

    logger.info(f"ST 子服务监听 {args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
