#!/usr/bin/env python3
"""
AMM - GGUF 模型量化转换引擎 (v0.4)
=====================================
基于 llama.cpp 的 llama-quantize 工具，将模型转换为不同精度的 GGUF。

支持的量化类型:
- 浮点: fp16, bf16 (BF16, 需支持), f32
- 低位: q4_0, q4_1, q5_0, q5_1, q8_0
- K-量化: q2_k, q3_k, q4_k, q5_k, q6_k, q8_k  (含 q4_k_m: 通过 --token-embedding-type/q4_k + moe)
- fp8: fp8_e4m3 (llama.cpp 新版 GGUF 支持)
"""
import os
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from aiohttp import web

logger = logging.getLogger("AMM.Quantizer")

# llama.cpp 量化器支持的类型映射（名称 -> 命令行 type 名）
# 基于本机 llama.cpp 实测的 ggml_type 枚举；fp8 需 llama.cpp 新版本 GGUF 支持，本版(BF16=30 无 F8)不支持
QUANT_TYPES = {
    # ---- 精度型 ----
    "f32":    "f32",        # 32 位浮点（无损）
    "fp16":   "f16",        # 16 位浮点
    "bf16":   "bf16",       # bfloat16
    # ---- 整数K量化 ----
    "q4_0":   "q4_0",
    "q4_1":   "q4_1",
    "q5_0":   "q5_0",
    "q5_1":   "q5_1",
    "q8_0":   "q8_0",
    # ---- K-混合（MoE 友好）----
    "q2_k":   "q2_k",
    "q3_k":   "q3_k",
    "q4_k":   "q4_k",
    "q4_k_m": "q4_k_m",     # MoE 变体：attention 用 q4_k, FFN 用不同
    "q5_k":   "q5_k",
    "q6_k":   "q6_k",
    "q8_k":   "q8_k",
    # ---- 新 IQ 量化 ----
    "iq4_xs":  "iq4_xs",
    "iq3_xxs": "iq3_xxs",
}


class QuantizeTask:
    def __init__(self, task_id: str, src: str, dst: str, qtype: str, out_path: str):
        self.task_id = task_id
        self.src = src
        self.dst = dst
        self.qtype = qtype
        self.out_path = out_path
        self.status = "pending"       # pending | running | done | failed
        self.created = time.time()
        self.finished = None
        self.error = ""
        self.detail = ""
        self.proc = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "source": self.src,
            "quant_type": self.qtype,
            "out_path": self.out_path,
            "status": self.status,
            "created_at": self.created,
            "finished_at": self.finished,
            "error": self.error,
            "detail": self.detail,
        }


class QuantizerBridge:
    def __init__(self, manager):
        self.manager = manager
        self.tasks: Dict[str, QuantizeTask] = {}
        self.models_dir = os.environ.get("MODELS_DIR", "/models")
        # llama-quantize 二进制位置
        self._bin = None
        for probe in [
            "/amm/backend/engines_installed/llama_cpp/b4727/bin/llama-quantize",
            "/usr/local/bin/llama-quantize",
        ]:
            if os.path.isfile(probe):
                self._bin = probe
                break

    # ------------------------------------------------------------
    # API
    # ------------------------------------------------------------
    def list_types(self, req) -> web.Response:
        return self._json({
            "binary": self._bin or "未找到 llama-quantize（需编译）",
            "quant_types": {k: v for k, v in QUANT_TYPES.items()},
        })

    async def quantize(self, req) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)

        src = (body.get("source") or "").strip()
        qtype = (body.get("quant_type") or "q4_k_m").strip().lower()
        out_name = (body.get("out_name") or "").strip()
        allow_requant = bool(body.get("allow_requantize", True))  # 默认允许从已量化源重转
        if not src:
            return self._json({"error": "缺少 source 模型文件路径"}, 400)
        if qtype not in QUANT_TYPES:
            return self._json({"error": f"不支持的量化类型 {qtype}，可选: {list(QUANT_TYPES)}"}, 400)
        if not self._bin:
            return self._json({"error": "llama-quantize 未安装/未编译"}, 503)

        # 解析源路径（相对 /models 或绝对）
        src_path = self._resolve(src)
        if not src_path or not os.path.isfile(src_path):
            return self._json({"error": f"源模型文件不存在: {src}"}, 404)

        out_name = out_name or (Path(src_path).stem + f"-{qtype}")
        if not out_name.lower().endswith(".gguf"):
            out_name += ".gguf"
        # 输出目录：优先 out_dir 请求参数(相对 /models 或绝对)，默认源同目录/gguf-converted
        req_out = (body.get("out_dir") or "").strip()
        if req_out:
            op = Path(req_out)
            if not op.is_absolute():
                op = Path(self.models_dir) / req_out
            op.mkdir(parents=True, exist_ok=True)
            out_dir = op
        else:
            out_dir = Path(src_path).parent / "gguf-converted"
            out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / out_name)

        task_id = f"q_{int(time.time())}"
        task = QuantizeTask(task_id, src_path, out_path, qtype, out_path)
        task.allow_requantize = allow_requant
        self.tasks[task_id] = task
        # 后台执行
        import threading
        threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return self._json({"ok": True, "task_id": task_id, **task.to_dict()})

    def status(self, req) -> web.Response:
        task_id = req.query.get("task_id", "")
        if task_id:
            t = self.tasks.get(task_id)
            return self._json(t.to_dict() if t else {"error": "任务不存在"}, 200 if t else 404)
        tasks = sorted([t.to_dict() for t in self.tasks.values()],
                       key=lambda x: x.get("created_at", 0), reverse=True)
        return self._json({"tasks": tasks[:20], "count": len(tasks)})

    # ------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------
    def _run(self, task: QuantizeTask):
        try:
            task.status = "running"
            task.detail = "转换中..."
            # llama-quantize <input.gguf> <output.gguf> <type>
            cmd = [self._bin, task.src, task.out_path, QUANT_TYPES[task.qtype]]
            # 允许从已量化源重转 (默认开启, 否则 q6_K/K 源转 q4 会被拒)
            allow = getattr(task, "allow_requantize", True)
            if allow:
                cmd.insert(1, "--allow-requantize")
            logger.info(f"量化命令: {' '.join(cmd)}")
            task.proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            task.finished = time.time()
            if task.proc.returncode == 0:
                task.status = "done"
                task.detail = f"输出: {task.out_path}"
            else:
                task.status = "failed"
                task.error = (task.proc.stderr or "")[-500:] or "转换失败"
        except subprocess.TimeoutExpired:
            task.status = "failed"
            task.error = "转换超时"
            task.finished = time.time()
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.finished = time.time()

    def _json(self, data, status=200):
        return web.json_response(data, status=status,
                                 headers={"Access-Control-Allow-Origin": "*"})

    # ------------------------------------------------------------
    # vLLM(safetensors/HF) -> GGUF 转换 (v0.5)
    # 流程: convert_hf_to_gguf.py 转出基础精度 GGUF, 如需量化再用 llama-quantize
    # ------------------------------------------------------------
    def _resolve_dir(self, path: str) -> Optional[str]:
        """解析目录(相对models_dir或绝对), 用于选 HF 模型目录"""
        p = Path(path)
        if p.is_absolute():
            return str(p) if p.exists() else None
        cand = Path(self.models_dir) / path
        return str(cand) if cand.exists() else None

    def _resolve(self, path: str) -> Optional[str]:
        """解析文件路径(相对 /models 或绝对路径), 返回存在路径或 None"""
        p = Path(path)
        if p.is_absolute():
            return str(p) if p.exists() else None
        cand = Path(self.models_dir) / path
        return str(cand) if cand.exists() else None

    async def convert_hf(self, req) -> web.Response:
        """POST /api/convert/hf  vLLM(HF safetensors) -> GGUF
        body: { source: vllm模型目录/文件, out_dir: 输出目录, model: 模型名,
                outtype: f32|f16|bf16 (基础精度, 默认f32),
                quant: 可选, 转完后再量化的精度(如 q4_k_m/fp16) }
        """
        try:
            body = await req.json()
        except Exception:
            return self._json({"error": "无效 JSON"}, 400)
        src = (body.get("source") or "").strip()
        if not src:
            return self._json({"error": "缺少 source"}, 400)
        src_path = self._resolve_dir(src) or self._resolve(src)
        if not src_path or not os.path.exists(src_path):
            return self._json({"error": f"源不存在: {src}"}, 404)

        out_type = (body.get("outtype") or "f32").strip().lower()
        if out_type not in ("f32", "f16", "bf16", "q8_0"):
            out_type = "f32"
        model_name = (body.get("model_name") or "").strip() or "model"
        # 输出目录
        out_dir = (body.get("out_dir") or "").strip()
        if out_dir:
            op = Path(out_dir)
            if not op.is_absolute():
                op = Path(self.models_dir) / out_dir
            op.mkdir(parents=True, exist_ok=True)
            dst_dir = op
        else:
            # 默认源目录/gguf-converted
            dst_dir = Path(src_path if os.path.isdir(src_path) else os.path.dirname(src_path)) / "gguf-converted"
            dst_dir.mkdir(parents=True, exist_ok=True)

        base_name = model_name if model_name.lower().endswith(".gguf") else model_name + ".gguf"
        base_path = str(dst_dir / base_name)
        convert_script = "/amm/backend/engines_installed/llama_cpp/b4727/scripts/convert_hf_to_gguf.py"
        if not os.path.exists(convert_script):
            return self._json({"error": "convert_hf_to_gguf.py 未部署"}, 503)

        task_id = f"vg_{int(time.time())}"
        task = QuantizeTask(task_id, src_path, base_path, out_type, base_path)
        quant = (body.get("quant") or "").strip().lower()
        task.kind = "convert_hf"
        task.quant = quant
        self.tasks[task_id] = task
        import threading
        threading.Thread(target=self._run_convert_hf, args=(task, convert_script, out_type), daemon=True).start()
        return self._json({"ok": True, "task_id": task_id, **task.to_dict()})

    def _run_convert_hf(self, task: QuantizeTask, script: str, out_type: str):
        try:
            task.status = "running"
            task.detail = "HF→GGUF 转换中..."
            py = "/amm/backend/engines_installed/vllm/0.22.1/venv/bin/python"
            cmd = [py, script, task.src, "--outfile", task.out_path, "--outtype", out_type]
            logger.info(f"HF转换命令: {' '.join(cmd)}")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, cwd="/amm/backend/engines_installed/llama_cpp/b4727/scripts",
                               env={**os.environ, "PYTHONPATH": "/amm/backend/engines_installed/llama_cpp/b4727/scripts"})
            if r.returncode != 0:
                task.status = "failed"; task.error = (r.stderr or r.stdout or "")[-600:]; task.finished = time.time(); return
            # 若需量化
            q = getattr(task, "quant", "")
            if q and q != out_type and q in QUANT_TYPES:
                task.detail = "GGUF 已生成，正在量化..."
                qcmd = [self._bin, "--allow-requantize", task.out_path, task.out_path + f".{q}.gguf", QUANT_TYPES[q]]
                qr = subprocess.run(qcmd, capture_output=True, text=True, timeout=7200)
                if qr.returncode == 0:
                    task.out_path = task.out_path + f".{q}.gguf"
                else:
                    task.detail = "基础GGUF已生成，量化失败: " + (qr.stderr or "")[-200:]
            task.status = "done"
            task.detail += f" 输出: {task.out_path}"
            task.finished = time.time()
        except Exception as e:
            task.status = "failed"; task.error = str(e); task.finished = time.time()


def setup_routes(app: web.Application, manager):
    bridge = QuantizerBridge(manager)
    app.router.add_get("/api/quantize/types", bridge.list_types)
    app.router.add_post("/api/quantize", bridge.quantize)
    app.router.add_get("/api/quantize/status", bridge.status)
    app.router.add_post("/api/convert/hf", bridge.convert_hf)