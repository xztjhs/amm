"""
OpenAI Compatible API Bridge
============================
提供 /v1/chat/completions 和 /v1/embeddings 兼容接口，
将请求转发到对应的 llama-server / vLLM / diffusers 后端。
"""
import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger("AMM.OpenAIBridge")


class OpenAIBridgeHandler:
    """OpenAI API 兼容处理器"""

    def __init__(self, manager):
        self.manager = manager

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                  headers={"Access-Control-Allow-Origin": "*"})

    # ================================================================
    # Helpers
    # ================================================================

    def _get_model_inst(self, model_id: str):
        return self.manager.get_instance(model_id)

    async def _forward_to_llama(self, inst, endpoint: str, body: Dict) -> Dict:
        """转发请求到 llama-server HTTP API"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/{endpoint}"
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Forward to llama-server failed: {e}")
            raise

    async def _forward_to_vllm(self, inst, endpoint: str, body: Dict) -> Dict:
        """转发请求到 vLLM OpenAI API"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/{endpoint}"
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Forward to vllm failed: {e}")
            raise

    async def _make_completion(self, inst, body: Dict) -> Dict:
        """统一的 completion 请求处理"""
        engine_type = inst.engine_type
        if engine_type == "llama_cpp":
            return await self._forward_to_llama(inst, "v1/chat/completions", body)
        elif engine_type == "vllm":
            return await self._forward_to_vllm(inst, "v1/chat/completions", body)
        else:
            raise ValueError(f"Unsupported engine for completions: {engine_type}")

    async def _make_embedding(self, inst, body: Dict) -> Dict:
        """统一的 embedding 请求处理"""
        engine_type = inst.engine_type
        if engine_type == "llama_cpp":
            return await self._forward_to_llama(inst, "v1/embeddings", body)
        elif engine_type == "vllm":
            return await self._forward_to_vllm(inst, "v1/embeddings", body)
        else:
            raise ValueError(f"Unsupported engine for embeddings: {engine_type}")

    # ================================================================
    # OpenAI API Endpoints
    # ================================================================

    async def models_list(self, req):
        """GET /v1/models"""
        data = {"object": "list", "data": []}
        for key in self.manager._model_keys():
            cfg = self.manager.config.get(key)
            if not cfg:
                continue
            inst = self.manager.get_instance(cfg["id"])
            if not inst:
                continue
            data["data"].append({
                "id": cfg["id"],
                "object": "model",
                "created": 0,
                "owned_by": "amm",
            })
        return self._json(data)

    async def chat_completions(self, req):
        """POST /v1/chat/completions"""
        try:
            body = await req.json()
            model_param = body.get("model", "chat")
            model_id = model_param if model_param in self.manager.instances else "chat"

            inst = self._get_model_inst(model_id)
            if not inst:
                return self._json({"error": "Model not found"}, 404)
            if inst.status != "running":
                return self._json({"error": f"Model {model_id} is not running"}, 503)

            result = await self._make_completion(inst, body)
            return self._json(result)

        except Exception as e:
            logger.exception("chat_completions error")
            return self._json({"error": str(e)}, 500)

    async def embeddings(self, req):
        """POST /v1/embeddings"""
        try:
            body = await req.json()
            model_param = body.get("model", "embedding")
            model_id = model_param if model_param in self.manager.instances else "embedding"

            inst = self._get_model_inst(model_id)
            if not inst:
                return self._json({"error": "Model not found"}, 404)
            if inst.status != "running":
                return self._json({"error": f"Model {model_id} is not running"}, 503)

            result = await self._make_embedding(inst, body)
            return self._json(result)

        except Exception as e:
            logger.exception("embeddings error")
            return self._json({"error": str(e)}, 500)

    # ================================================================
    # Streaming support (placeholder - pass-through)
    # ================================================================

    async def chat_completions_stream(self, req):
        """POST /v1/chat/completions with stream=true - proxy stream"""
        try:
            body = await req.json()
            model_param = body.get("model", "chat")
            model_id = model_param if model_param in self.manager.instances else "chat"

            inst = self._get_model_inst(model_id)
            if not inst or inst.status != "running":
                return self._json({"error": "Model not running"}, 503)

            # Stream proxy
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/v1/chat/completions"
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if body.get("stream"):
                        # SSE streaming
                        response = web.StreamResponse(
                            status=resp.status,
                            headers={
                                "Content-Type": "text/event-stream",
                                "Cache-Control": "no-cache",
                                "Access-Control-Allow-Origin": "*",
                            },
                        )
                        await response.prepare(req)
                        async for chunk in resp.content:
                            await response.write(chunk)
                        return response
                    else:
                        data = await resp.json()
                        return self._json(data)

        except Exception as e:
            logger.exception("chat_completions_stream error")
            return self._json({"error": str(e)}, 500)


def setup_routes(app: web.Application, manager):
    """注册 OpenAI 兼容路由"""
    h = OpenAIBridgeHandler(manager)
    app.router.add_get("/v1/models", h.models_list)
    app.router.add_post("/v1/chat/completions", h.chat_completions_stream)
    app.router.add_post("/v1/embeddings", h.embeddings)
