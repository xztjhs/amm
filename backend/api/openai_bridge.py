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
        """转发请求到 vLLM OpenAI API

        AMM 侧模型 id 为内部 id(如 "chat")，而 vLLM 端 served model 名是模型路径
        (如 /models/Qwen3-4B)，因此转发前把 body["model"] 重写为 vLLM 能识别的名字。
        """
        try:
            model_id = os.environ.get("MODELS_DIR", "/models") + "/" + inst.selected_model_file
            # 去掉尾部路径分隔符，得到 vLLM 的 served model 名（即 --model 参数）
            vllm_model = model_id.rstrip("/")
            body = dict(body)
            body["model"] = vllm_model
            # 硬写入文件调试，绕过 logger 可能未输出的问题
            try:
                import traceback
                with open("/tmp/amm_bridge_debug.txt", "a") as f:
                    f.write(f"rewrote model -> {vllm_model} endpoint={endpoint}\n")
            except Exception:
                pass
            logger.info(f"[vllm-bridge] rewrote model '{body.get('model')}' -> '{vllm_model}' endpoint={endpoint}")
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
    # ASR / TTS / OCR bridges (OpenAI compatible)
    # llama-server 本身即为 OpenAI 兼容端点，这里做透传转发。
    # ================================================================

    def _resolve_model_id(self, body: Dict, fallback: str) -> str:
        """从请求体解析模型 id，结合 body 中的 model 字段与 fallback"""
        m = body.get("model", "")
        if m in self.manager.instances:
            return m
        return fallback

    async def _forward_audio(self, inst, endpoint: str, body: Dict):
        """（保留）转发 JSON body 到 llama-server audio 端点 (TTS)"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/{endpoint}"
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    data = await resp.read()
                    return web.Response(status=resp.status, body=data, content_type=resp.content_type or "audio/wav")
        except Exception as e:
            logger.error(f"forward audio {endpoint} failed: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def asr_transcriptions(self, req):
        """POST /v1/audio/transcriptions - 语音转文字

        接收 OpenAI 格式 multipart（file + model），读取文件内容后
        重新组装 multipart 转发给 llama-server。
        """
        try:
            inst = self._get_model_inst("asr")
            if not inst:
                return self._json({"error": "ASR 模型未配置"}, 404)
            if inst.status != "running":
                return self._json({"error": "ASR model is not running"}, 503)

            # 读取 multipart 所有字段
            reader = await req.multipart()
            filename = "audio.wav"
            audio_bytes = None
            extra_fields = []
            async for part in reader:
                name = part.name
                if part.filename:
                    filename = part.filename
                    audio_bytes = await part.read()
                else:
                    value = (await part.read()).decode("utf-8", "ignore")
                    extra_fields.append((name, value))
            if audio_bytes is None:
                return self._json({"error": "缺少音频文件字段 (file)"}, 400)

            # 重新组装 multipart 转发给 llama-server
            form = aiohttp.FormData()
            form.add_field("file", audio_bytes, filename=filename, content_type="application/octet-stream")
            for k, v in extra_fields:
                form.add_field(k, v)

            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/v1/audio/transcriptions"
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    text = await resp.text()
                    return web.Response(status=resp.status, text=text, content_type=resp.content_type)
        except Exception as e:
            logger.exception("asr_transcriptions error")
            return self._json({"error": str(e)}, 500)

    async def tts_speech(self, req):
        """POST /v1/audio/speech - 文字转语音

        llama-tts 是 CLI 按需工具（非常驻 server），这里直接调用生成
audio 并返回。模型文件与 mmproj 从 AMM 配置读取。
        """
        try:
            body = await req.json()
            text = body.get("input", "")
            if not isinstance(text, str) or not text.strip():
                return self._json({"error": "input (text) required"}, 400)
            text = text.strip()

            # 获取 TTS 实例/配置
            inst = self._get_model_inst("tts")
            if not inst:
                return self._json({"error": "TTS 模型未配置"}, 404)

            models_dir = os.environ.get("MODELS_DIR", "/models")
            model_file = inst.selected_model_file
            mo = self.manager._find_model_config("tts")
            avail = (mo or {}).get("available_models", [{}])
            mmproj = None
            if avail and avail[0].get("mmproj"):
                mmproj = avail[0]["mmproj"]
            # 优先用实例参数里保存的 mmproj
            iparams = inst.parameters or {}
            if iparams.get("mmproj"):
                mmproj = iparams["mmproj"]
            tts_lang = iparams.get("tts_lang", "zh")

            model_path = os.path.join(models_dir, model_file)
            mmproj_path = os.path.join(models_dir, mmproj) if mmproj else ""
            if not os.path.isfile(model_path):
                return self._json({"error": f"TTS 模型文件不存在: {model_path}"}, 404)
            if not mmproj_path or not os.path.isfile(mmproj_path):
                return self._json({"error": f"TTS mmproj 不存在: {mmproj_path}"}, 404)

            # prompt 长度 -> 帧数限制（约 12Hz，字数*8+200 帧起步）
            n_frames = min(2000, max(200, len(text) * 15))

            cmd = [
                "/usr/local/bin/llama-tts",
                "-m", model_path,
                "-mm", mmproj_path,
                "-p", text,
                "--tts-lang", tts_lang,
                "-ngl", "999",
                "-n", str(n_frames),
                "--output", "/tmp/amm_tts_out.wav",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                return self._json({"error": "TTS generation timeout"}, 504)

            if not os.path.isfile("/tmp/amm_tts_out.wav"):
                err = (stderr or b"").decode("utf-8", "ignore")[-500:]
                logger.error(f"llama-tts failed: {err}")
                return self._json({"error": f"TTS 生成失败: {err}"}, 500)

            with open("/tmp/amm_tts_out.wav", "rb") as f:
                data = f.read()
            os.remove("/tmp/amm_tts_out.wav")
            return web.Response(status=200, body=data, content_type="audio/wav")

        except Exception as e:
            logger.exception("tts_speech error")
            return self._json({"error": str(e)}, 500)

    # OCR 复用 chat/completions（视觉多模态），OpenAI 兼容即可，无需独立端点。
    # 但提供显式 /v1/ocr 别名提升可发现性。
    async def ocr_handler(self, req):
        """POST /v1/ocr - 传入图片做文字识别（转 chat 视觉请求）"""
        try:
            body = await req.json()
            inst = self._get_model_inst("ocr")
            if not inst:
                return self._json({"error": "OCR 模型未配置"}, 404)
            if inst.status != "running":
                return self._json({"error": "OCR model is not running"}, 503)

            # 构造多模态 chat 请求
            image_urls = body.get("images", []) or []
            prompt = body.get("prompt", "请识别图片中的文字，完整输出。")
            content: list = []
            for u in image_urls:
                content.append({"type": "image_url", "image_url": {"url": u}})
            content.append({"type": "text", "text": prompt})

            chat_body = {
                "model": "ocr",
                "messages": [{"role": "user", "content": content}],
                "max_tokens": body.get("max_tokens", 2048),
                "temperature": body.get("temperature", 0.2),
            }
            result = await self._make_completion(inst, chat_body)
            return self._json(result)
        except Exception as e:
            logger.exception("ocr_handler error")
            return self._json({"error": str(e)}, 500)

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

    async def _rewrite_vllm_model(self, inst, body: Dict) -> Dict:
        """把 AMM 内部模型 id 重写为 vLLM 端 served model 名（模型路径）"""
        body = dict(body)
        vllm_model = (os.environ.get("MODELS_DIR", "/models") + "/" + inst.selected_model_file).rstrip("/")
        body["model"] = vllm_model
        return body

    async def chat_completions_stream(self, req):
        """POST /v1/chat/completions with stream=true - proxy stream"""
        try:
            body = await req.json()
            model_param = body.get("model", "chat")
            model_id = model_param if model_param in self.manager.instances else "chat"

            inst = self._get_model_inst(model_id)
            if not inst or inst.status != "running":
                return self._json({"error": "Model not running"}, 503)

            # 若引擎为 vllm，把 model 重写为 vLLM 端 served 名（模型路径）
            if inst.engine_type == "vllm":
                body = self._rewrite_vllm_model(inst, body)

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
    # ASR / TTS / OCR
    app.router.add_post("/v1/audio/transcriptions", h.asr_transcriptions)
    app.router.add_post("/v1/audio/speech", h.tts_speech)
    app.router.add_post("/v1/ocr", h.ocr_handler)
