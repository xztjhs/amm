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
import time
from typing import Dict, Any, Optional

import aiohttp
from aiohttp import web

from backend.core.client_compat import instance_meta
from backend.core.router import _params, _stream_output, _summarize
from backend.core.sse_capture import SSETextCapture

logger = logging.getLogger("AMM.OpenAIBridge")


class OpenAIBridgeHandler:
    """OpenAI API 兼容处理器"""

    def __init__(self, manager, app=None):
        self.manager = manager
        self._app = app

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                  headers={"Access-Control-Allow-Origin": "*"})

    # ================================================================
    # Helpers
    # ================================================================

    def _get_model_inst(self, model_id: str):
        return self.manager.get_instance(model_id)

    def _resolve_inst(self, req_model_id: str, category: str):
        """按 category 解析实例，优先用请求里指定的 model。

        v1.1 起同一类别可以有多个动态实例（例如两个 embedding），
        调用方通过 body.model = <uid> 指定；不指定时回退到该类别里
        第一个 running 的实例，最后才用固定的 9 类场景 id。
        """
        if req_model_id:
            inst = self.manager.get_instance(req_model_id)
            if inst and inst.category == category:
                return inst
        for inst in self.manager.get_all_instances().values():
            if inst.category == category and inst.status == "running":
                return inst
        return self.manager.get_instance(category)

    def _resolve_tts_binary(self, inst) -> str:
        """定位 llama-tts 可执行文件（llama.cpp tools/tts，与 llama-server 同目录）。"""
        roots = []
        eng_root = os.environ.get("AMM_ENGINES_ROOT", "")
        if eng_root:
            roots.append(os.path.join(eng_root, "llama_cpp"))
        roots.append(os.path.join(os.environ.get("AMM_ROOT", "/amm"),
                                  "backend", "engines_installed", "llama_cpp"))
        # 先试实例自己的版本+flavor，再按目录倒序试其它版本
        prefer = []
        if getattr(inst, "engine_version", "") and getattr(inst, "flavor", ""):
            prefer.append(f"{inst.engine_version}-{inst.flavor}")
        for root in roots:
            if not os.path.isdir(root):
                continue
            for d in prefer + sorted(os.listdir(root), reverse=True):
                p = os.path.join(root, d, "bin", "llama-tts")
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
        for p in ("/usr/local/bin/llama-tts",):
            if os.path.isfile(p):
                return p
        return ""

    @staticmethod
    def _sanitize_schema(body):
        """递归移除 JSON Schema 里 llama.cpp 不接受的约束关键字。

        本项目 llama.cpp(b4727) 在做 JSON Schema -> grammar 转换时，要求 pattern
        必须是 ^...$ 锚定的正则，否则直接 HTTP 400：
           "JSON schema conversion failed: Pattern must start with '^' and end with '$'"
        传入的 tools/response_format json_schema 里若含未锚定 pattern（如 OpenClaw
        文件引用 schema 的 pattern），整段请求会被拒。这里把仅属“可选收窄”的约束去掉
        （pattern 及长度约束），让 llama 正常转换，同时保留工具调用能力。
        """
        _strip = {"pattern", "minLength", "maxLength", "minItems", "maxItems", "minContains", "maxContains"}
        try:
            if isinstance(body, dict):
                return {k: OpenAIBridgeHandler._sanitize_schema(v) for k, v in body.items() if k not in _strip}
            if isinstance(body, list):
                return [OpenAIBridgeHandler._sanitize_schema(v) for v in body]
            return body
        except Exception:
            return body

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
            body = self._rewrite_vllm_model(inst, body)
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
            result = await self._forward_to_llama(inst, "v1/embeddings", body)
            # llama-server 没带 --embeddings 时回 501，原样透传客户端根本看不懂。
            # 这里翻译成可执行的中文提示（实例名 + 该改哪个参数）。
            err = result.get("error") if isinstance(result, dict) else None
            msg = ""
            if isinstance(err, dict):
                msg = err.get("message") or ""
            elif isinstance(err, str):
                msg = err
            if "does not support embeddings" in msg or (
                    isinstance(err, dict) and err.get("code") == 501):
                raise ValueError(
                    f"实例 {inst.model_id} 没有以 embedding 模式启动。"
                    f"请在 Running Models 里把它的 embeddings 参数设为 true 后重启"
                    f"（或重建为 embedding 类别）。")
            return result
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
            inst = self._resolve_inst(req.query.get("model", ""), "asr")
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
            inst = self._resolve_inst(body.get("model", ""), "tts")
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

            # llama-tts 是 llama.cpp 的独立工具（tools/tts），不在 llama-server 里。
            # 引擎目录里的各版本会各自编出 bin/llama-tts；优先用实例对应的版本，
            # 再回退到其它已安装版本 / 镜像内置路径。
            tts_bin = self._resolve_tts_binary(inst)
            if not tts_bin:
                return self._json({
                    "error": "未找到 llama-tts 可执行文件",
                    "hint": "在 Settings → 推理引擎管理 重新安装 llama.cpp，"
                            "安装脚本会一并编译 llama-tts",
                }, 503)

            out_wav = "/tmp/amm_tts_out.wav"
            if os.path.exists(out_wav):
                os.remove(out_wav)

            cmd = [
                tts_bin,
                "-m", model_path,
                "--mmproj", mmproj_path,
                "-p", text,
                "--tts-lang", tts_lang,
                "-ngl", "0" if inst.flavor == "cpu" else "999",
                "-n", str(n_frames),
                "--output", out_wav,
            ]

            env = dict(os.environ)
            # 动态链接的 libggml/libllama 在构建目录里，补进 LD_LIBRARY_PATH
            bdir = os.path.dirname(os.path.dirname(tts_bin))
            for extra in (os.path.join(bdir, "build-cpu", "bin"),
                          os.path.join(bdir, "build-cuda", "bin"),
                          os.path.dirname(tts_bin)):
                if os.path.isdir(extra):
                    env["LD_LIBRARY_PATH"] = extra + ":" + env.get("LD_LIBRARY_PATH", "")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
                env=env,
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
            inst = self._resolve_inst(body.get("model", ""), "ocr")
            if not inst:
                return self._json({"error": "OCR 模型未配置"}, 404)
            if inst.status != "running":
                return self._json({"error": "OCR model is not running"}, 503)

            # 构造多模态 chat 请求
            # 兼容三种入参：images=[...]（列表）、image="<base64 或 url>"（单个）、
            # image_url="<url>"。纯 base64 自动补 data:image/png;base64, 前缀，
            # 否则 llama-server 会当成普通文本，模型就“看不到图”。
            image_urls = list(body.get("images") or [])
            for key in ("image", "image_url"):
                single = body.get(key)
                if isinstance(single, str) and single.strip():
                    image_urls.append(single.strip())

            def _norm(u: str) -> str:
                if u.startswith(("http://", "https://", "data:")):
                    return u
                return "data:image/png;base64," + u

            image_urls = [_norm(u) for u in image_urls if isinstance(u, str) and u.strip()]
            if not image_urls:
                return self._json({
                    "error": "缺少图片",
                    "hint": "传 image（base64 或 URL）、images（数组）或 image_url",
                }, 400)

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
        """GET /v1/models

        owned_by 用 Settings 里配置的 node_name（默认 AMM），
        而不是随机 UUID —— 客户端（如 Cherry Studio / Open WebUI）会把它
        当「分组」显示，UUID 对用户毫无意义。

        只列出**当前可调用**的模型（running）。以前把 9 个固定场景 id
        （chat/embedding/asr…）无条件列出来，即使实例 stopped，
        客户端会把这些占位名当成真模型去调用，然后报各种奇怪错误。

        每条本地模型都带 `name` / `description` / `capabilities`：
        客户端（如 Cherry Studio）在没有 name 时会拿 id 去模型名注册表
        模糊匹配，`qwen3` 会被认成 "Qwen3 14B" 并且丢掉 embedding 能力。
        名字与 id 不同时客户端优先用我们的，能避开这类误判。
        """
        sc = (self.manager.config or {}).get("server", {}) or {}
        node = (sc.get("node_name") or "").strip() or "AMM"
        data = {"object": "list", "data": []}
        seen = set()
        for key in self.manager._model_keys():
            cfg = self.manager.config.get(key)
            if not cfg:
                continue
            inst = self.manager.get_instance(cfg["id"])
            # 固定场景槽位：仅在真正运行时才对外可见
            if not inst or inst.status != "running":
                continue
            seen.add(cfg["id"])
            meta = instance_meta(inst)
            data["data"].append({
                "id": cfg["id"],
                "object": "model",
                "created": 0,
                "owned_by": node,
                "name": meta["name"],
                "description": meta["description"],
                "capabilities": meta["capabilities"],
                "category": inst.category,
            })
        # v1.1: 动态实例（端口 18100+）也应出现在模型列表里，便于客户端直接选
        for uid, inst in self.manager.get_all_instances().items():
            if uid in seen or not getattr(inst, "is_dynamic", False):
                continue
            if inst.status != "running":
                continue
            meta = instance_meta(inst)
            data["data"].append({
                "id": uid,
                "object": "model",
                "created": 0,
                "owned_by": node,
                "name": meta["name"],
                "description": meta["description"],
                "capabilities": meta["capabilities"],
                "category": inst.category,
                "status": inst.status,
                "port": inst.port,
            })
        # 外部 provider 汇聚进来的模型也必须出现在这里 —— 客户端只认 /v1/models，
        # 少了这一段，用户在 Token Router 里拉到的模型客户端根本看不到。
        router = None
        try:
            if self._app is not None:
                router = self._app.get("token_router")
        except Exception:
            router = None
        if router is not None:
            try:
                for m in router.all_models():
                    mid = m.get("id")
                    if not mid or mid in seen:
                        continue
                    # /api/router/models 的 owned_by 对本地是 "amm-local"，
                    # 对外部是 provider 返回的 owner（如 epai）或 "provider:<name>"。
                    # 这里只排除本地实例（已在上面单独处理），其余都视为外部模型。
                    owner = m.get("owned_by") or ""
                    if owner == "amm-local":
                        continue
                    seen.add(mid)
                    prov = m.get("provider") or ""
                    if owner.startswith("provider:"):
                        owner_name = owner.split(":", 1)[1] or node
                    else:
                        owner_name = prov or owner or node
                    item = {
                        "id": mid,
                        "object": "model",
                        "created": m.get("created", 0) or 0,
                        "owned_by": owner_name,
                    }
                    if m.get("pricing_cny_per_mtok"):
                        item["pricing_cny_per_mtok"] = m["pricing_cny_per_mtok"]
                    data["data"].append(item)
            except Exception as e:
                logger.warning(f"/v1/models 汇总 provider 模型失败: {e}")
        return self._json(data)

    def _resolve_chat_target(self, model_param: str):
        """解析一次 chat 请求该发往哪里。

        返回 (kind, payload)：
          ("instance", inst)   本地运行实例
          ("provider", decision) 外部 provider（需走 relay）
          ("error", dict)      明确失败

        关键：绝不能把未知模型名静默改成 "chat"。
        之前 `model_id = model_param if ... else "chat"` 会让
        「请求 glm-5.3」在 chat 运行时被本地 Qwen3.6 回答，
        客户端以为在用 GLM，实际是另一个模型 —— 结果完全错乱。
        """
        m = (model_param or "").strip()
        if not m:
            return "error", {"error": "缺少 model 参数"}

        # 0) 大小写不敏感匹配：客户端 UI 常把 "chat" 显示成 "Chat" 再原样回传，
        #    严格区分大小写会让它永远找不到模型（表现为 500/404 且原因难查）。
        if m not in self.manager.instances:
            low = m.lower()
            for cid in list(self.manager.instances.keys()):
                if cid.lower() == low:
                    m = cid
                    break

        # 1) 本地实例精确匹配
        inst = self.manager.instances.get(m)
        if inst is not None:
            if inst.status != "running":
                return "error", {"error": f"Model {m} is not running",
                                 "hint": "该实例未启动，请先在 Models 页 Start",
                                 "_status": 503}
            return "instance", inst

        # 2) 外部 provider（优先于任何回退）
        router = None
        try:
            if self._app is not None:
                router = self._app.get("token_router")
        except Exception:
            router = None
        if router is not None:
            try:
                decision = router.resolve(m)
            except Exception:
                decision = {}
            if decision.get("target") == "provider":
                return "provider", decision
            if decision.get("target") == "local-stopped":
                return "error", {
                    "error": f"Model {m} 未运行",
                    "hint": "实例存在但已停止，请在 Models 页 Start",
                }

        # 3) 实在找不到：只有当 m 就是某个固定槽位 id 时才考虑回退
        for slot in ("chat",):
            if m == slot:
                si = self.manager.instances.get(slot)
                if si is not None and si.status == "running":
                    return "instance", si

        return "error", {
            "error": f"模型不存在: {m}",
            "hint": "GET /v1/models 查看可用模型；外部模型需先在 Token Router 拉取",
        }

    async def chat_completions(self, req):
        """POST /v1/chat/completions"""
        try:
            body = await req.json()
            body = self._sanitize_schema(body)
            model_param = body.get("model", "chat")
            kind, payload = self._resolve_chat_target(model_param)
            if kind == "error":
                return self._json({"error": {"message": payload.get("error"),
                                             "type": "invalid_model",
                                             "hint": payload.get("hint", "")}},
                                  payload.get("_status", 404))
            if kind == "provider":
                return await self._app.get("token_router").relay_to_provider(
                    req, body, payload)

            inst = payload
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

            # 1) 客户端指定的实例（用户自建动态实例常用自定义 uid）
            inst = self.manager.get_instance(model_param) if model_param else None
            # 2) 没找到或不适合做 embedding（例如建实例时类别误选 chat），
            #    回退到任意「能出向量」的 running 实例：
            #    - category=embedding
            #    - 或模型文件是 Embedding 模型 / 实例开了 embeddings 参数
            if inst is None or not self._can_embed(inst):
                inst = self._pick_embedding_instance() or inst
            if inst is None:
                return self._json({
                    "error": f"Model not found: {model_param}",
                    "hint": "用 GET /v1/models 查看可用实例 id",
                }, 404)
            if inst.status != "running":
                return self._json({"error": f"Model {inst.model_id} is not running"}, 503)
            if not self._can_embed(inst):
                return self._json({
                    "error": f"实例 {inst.model_id} 不是 embedding 模型",
                    "hint": "该实例的模型文件或参数未启用 embeddings；"
                            "请在 Launch Model 里以 embedding 类别重建实例",
                    "category": inst.category,
                    "model_file": inst.selected_model_file,
                }, 400)

            result = await self._make_embedding(inst, body)
            # embeddings 也要进调用历史 —— 以前只有 chat 记录，
            # 导致 Log Center 里完全看不到向量调用（缺陷 DEV-02）
            try:
                n_in = len(body.get("input")) if isinstance(body.get("input"), list) \
                    else 1
                n_dim = len(((result or {}).get("data") or [{}])[0].get("embedding") or [])
                self._record_local_call(
                    inst, body,
                    f"[embedding] {n_in} 条输入 → {n_dim} 维向量",
                    {"inputs": n_in, "dim": n_dim})
            except Exception as e:
                logger.warning(f"embedding 调用历史写入失败: {e}")
            return self._json(result)

        except Exception as e:
            logger.exception("embeddings error")
            return self._json({"error": str(e)}, 500)

    @staticmethod
    def _looks_embedding_model(inst) -> bool:
        f = (inst.selected_model_file or "").lower()
        return ("embedding" in f) or ("bge-" in f) or ("gte-" in f) or ("m3e" in f)

    def _can_embed(self, inst) -> bool:
        if inst is None:
            return False
        if inst.category == "embedding":
            return True
        if (inst.parameters or {}).get("embeddings"):
            return True
        return self._looks_embedding_model(inst)

    def _pick_embedding_instance(self):
        """挑一个可用的 embedding 实例：优先 category=embedding 的 running 实例。"""
        best = None
        for inst in self.manager.get_all_instances().values():
            if inst.status != "running" or not self._can_embed(inst):
                continue
            if inst.category == "embedding":
                return inst
            best = best or inst
        return best

    # ================================================================
    # 调用历史（本地实例也写入，否则 Log Center 只有 provider 的一半）
    # ================================================================

    def _record_local_call(self, inst, body: Dict, output: str,
                           metrics: Optional[Dict] = None,
                           status: str = "ok") -> None:
        """把一次本地实例调用写入 Token Router 的 calls 记录。

        失败只记日志，绝不影响推理主流程。
        """
        try:
            router = self._app.get("token_router") if self._app is not None else None
            if router is None:
                return
            router.record_call({
                "ts": time.time(),
                "key": "local",
                "model": getattr(inst, "model_id", ""),
                "provider": "local",
                "status": status,
                "stream": bool(body.get("stream")),
                "input": _summarize(body),
                "output": output,
                "params": _params(body),
                "metrics": metrics or {},
            })
        except Exception as e:
            logger.warning(f"本地调用历史写入失败: {e}")

    def _record_local_stream(self, inst, body: Dict, cap,
                             t0: float = 0.0, emitted: int = 0,
                             first_at: Optional[float] = None) -> None:
        """流式调用结束后的记账（输出取累积文本，不再是 "(stream)"）"""
        metrics = {}
        if t0:
            e2e = (time.perf_counter() - t0) * 1000
            ttft = ((first_at or t0) - t0) * 1000
            out_tok = max(1, emitted // 4)
            metrics = {
                "e2e_ms": round(e2e, 1),
                "ttft_ms": round(ttft, 1),
                "out_tokens": out_tok,
                "tps": round(out_tok / (e2e / 1000), 1) if e2e > 0 else None,
            }
        self._record_local_call(inst, body, _stream_output(cap), metrics)

    # ================================================================
    # Streaming support (placeholder - pass-through)
    # ================================================================

    def _rewrite_vllm_model(self, inst, body: Dict) -> Dict:
        """把 AMM 内部模型 id 重写为 vLLM 端 served model 名（模型路径）"""
        body = dict(body)
        vllm_model = (os.environ.get("MODELS_DIR", "/models") + "/" + inst.selected_model_file).rstrip("/")
        body["model"] = vllm_model
        return body

    async def chat_completions_stream(self, req):
        """POST /v1/chat/completions with stream=true - proxy stream"""
        try:
            body = await req.json()
            body = self._sanitize_schema(body)
            model_param = body.get("model", "chat")
            kind, payload = self._resolve_chat_target(model_param)
            if kind == "error":
                return self._json({"error": {"message": payload.get("error"),
                                             "type": "invalid_model",
                                             "hint": payload.get("hint", "")}},
                                  payload.get("_status", 404))
            if kind == "provider":
                return await self._app.get("token_router").relay_to_provider(
                    req, body, payload)

            inst = payload
            # 记下客户端请求的名字，回包时原样返回，避免客户端以为换了模型
            client_model = model_param
            body = dict(body)

            # 若引擎为 vllm，把 model 重写为 vLLM 端 served 名（模型路径）
            if inst.engine_type == "vllm":
                body = self._rewrite_vllm_model(inst, body)
            else:
                # llama-server 用 --alias 或实例 id 才认；这里统一用实例 id
                body["model"] = inst.model_id

            # Stream proxy
            t0 = time.perf_counter()
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{inst.port}/v1/chat/completions"
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if body.get("stream"):
                        # SSE streaming：边转发边解析，结束时把真正的输出写进调用历史
                        response = web.StreamResponse(
                            status=resp.status,
                            headers={
                                "Content-Type": "text/event-stream",
                                "Cache-Control": "no-cache",
                                "Access-Control-Allow-Origin": "*",
                            },
                        )
                        await response.prepare(req)
                        cap = SSETextCapture()
                        first_at = None
                        emitted = 0
                        async for chunk in resp.content.iter_any():
                            if first_at is None:
                                first_at = time.perf_counter()
                            cap.feed(chunk)
                            await response.write(chunk)
                            emitted += len(chunk)
                        cap.close()
                        self._record_local_stream(inst, body, cap,
                                                  t0=t0, emitted=emitted,
                                                  first_at=first_at)
                        return response
                    else:
                        data = await resp.json()
                        _e2e = (time.perf_counter() - t0) * 1000
                        _txt = ""
                        try:
                            _txt = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                        except Exception:
                            _txt = ""
                        self._record_local_call(inst, body, str(_txt)[:1500],
                                                {"e2e_ms": round(_e2e, 1)})
                        return self._json(data)

        except Exception as e:
            # 把请求体的关键字段一起记下来，否则线上 500 完全无法定位
            try:
                _m = (body or {}).get("model") if isinstance(body, dict) else None
                _keys = sorted((body or {}).keys()) if isinstance(body, dict) else []
            except Exception:
                _m, _keys = None, []
            logger.exception(
                f"chat_completions_stream error model={_m} body_keys={_keys}: {e}")
            return self._json({"error": {"message": str(e),
                                         "type": "internal_error",
                                         "model": _m,
                                         "body_keys": _keys}}, 500)


    async def rerank(self, req):
        """POST /v1/rerank - 重排序 (llama.cpp --reranking)

        OpenAI 兼容: {query, documents:[...], top_n?}
        转发到 llama-server 的 /rerank 端点 (llama_cpp 引擎)。
        """
        try:
            body = await req.json()
            inst = self._resolve_inst(body.get("model", ""), "reranker")
            if not inst or inst.status != "running":
                return self._json({"error": "Reranker model is not running"}, 503)
            if inst.engine_type not in ("llama_cpp", "llama"):
                return self._json({"error": f"rerank 不支持引擎 {inst.engine_type}"}, 400)
            async with aiohttp.ClientSession() as session:
                # llama-server 用 /rerank (非 /v1/rerank)
                url = f"http://127.0.0.1:{inst.port}/rerank"
                async with session.post(url, json=body,
                                         timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    data = await resp.json()
                    # rerank 同样补记调用历史
                    try:
                        hits = data.get("results") or data.get("data") or []
                        top = hits[:3]
                        self._record_local_call(
                            inst, body,
                            f"[rerank] {len(hits)} 条命中，top: "
                            + json.dumps(top, ensure_ascii=False)[:300],
                            {"hits": len(hits)})
                    except Exception as e:
                        logger.warning(f"rerank 调用历史写入失败: {e}")
                    return self._json(data, status=resp.status)
        except Exception as e:
            logger.exception("rerank error")
            return self._json({"error": str(e)}, 500)


def setup_routes(app: web.Application, manager):
    """注册 OpenAI 兼容路由"""
    h = OpenAIBridgeHandler(manager, app)
    app.router.add_get("/v1/models", h.models_list)
    app.router.add_post("/v1/chat/completions", h.chat_completions_stream)
    app.router.add_post("/v1/embeddings", h.embeddings)
    # ASR / TTS / OCR
    app.router.add_post("/v1/audio/transcriptions", h.asr_transcriptions)
    app.router.add_post("/v1/audio/speech", h.tts_speech)
    app.router.add_post("/v1/ocr", h.ocr_handler)
    # Rerank (v0.2+)
    app.router.add_post("/v1/rerank", h.rerank)
