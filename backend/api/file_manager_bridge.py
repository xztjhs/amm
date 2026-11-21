"""
File Manager Bridge — 本地文件管理（#7）
=========================================
给 Settings 提供一个通用的文件管理器，受限在「允许的根目录」内：
浏览目录、查看/编辑/创建/删除/上传/下载。

安全边界（重要）：
  - 只允许在 AMM_FS_ROOTS / 模型目录 / 日志目录 / 数据目录下操作
  - 所有路径先 realpath 归一化，再校验是否落在允许根之下
    （防 `../` 穿越与符号链接逃逸）
  - 文本编辑统一按 UTF-8，超过 2MB 或二进制拒绝在浏览器里编辑
  - 删除默认进回收（AMM_ROOT/.trash），不是直接 unlink

路由：
  GET    /api/fm/roots                 允许的根目录列表
  GET    /api/fm/list?path=            目录列表（含 stat）
  GET    /api/fm/read?path=            读文本（带编码/大小/二进制判定）
  POST   /api/fm/write                 {path, content}  覆盖写（也可新建）
  POST   /api/fm/mkdir                 {path}
  POST   /api/fm/upload    (multipart) 上传到 ?path= 目录
  GET    /api/fm/download?path=        下载文件
  POST   /api/fm/delete                {paths:[], permanent:false}
  POST   /api/fm/rename                {path, new_name}
"""
import json
import logging
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

logger = logging.getLogger("AMM.FileManager")

MAX_EDIT_BYTES = 2 * 1024 * 1024      # 浏览器内编辑上限
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 单文件上传上限

# 二进制探测：命中即不允许在浏览器里编辑
_BINARY_EXT = {
    ".gguf", ".bin", ".safetensors", ".pt", ".pth", ".ckpt", ".onnx",
    ".zip", ".gz", ".tar", ".7z", ".xz", ".bz2", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".mp3", ".wav", ".flac", ".mp4", ".mov", ".mkv", ".webm",
    ".so", ".dylib", ".dll", ".pyc", ".whl", ".rpm", ".deb",
    ".pdf", ".docx", ".xlsx", ".pptx",
}


def _allowed_roots() -> List[Path]:
    """允许浏览的根目录（去重、只保留存在的）"""
    cands = []
    env = os.environ.get("AMM_FS_ROOTS", "")
    for p in env.split(os.pathsep):
        if p.strip():
            cands.append(p.strip())
    cands += [
        os.environ.get("AMM_ROOT", "/amm"),
        os.environ.get("MODELS_DIR", "/models"),
        "/amm/logs",
        "/amm/data",
        "/amm/templates",
        "/amm/verification",
    ]
    seen, out = set(), []
    for c in cands:
        try:
            rp = Path(c).resolve()
        except Exception:
            continue
        if rp in seen or not rp.exists():
            continue
        seen.add(rp)
        out.append(rp)
    return out


def _resolve(path: str) -> Path:
    """把用户给的路径归一化并校验在允许根下。失败抛 PermissionError。"""
    roots = _allowed_roots()
    raw = (path or "").strip() or (str(roots[0]) if roots else "/")
    # 支持相对路径：默认相对第一个根
    if not raw.startswith("/"):
        raw = str((roots[0] if roots else Path("/")) / raw)
    real = Path(raw).expanduser().resolve()
    for r in roots:
        if real == r or r in real.parents:
            return real
    raise PermissionError(f"路径不在允许范围内: {path}")


def _is_binary(p: Path) -> bool:
    if p.suffix.lower() in _BINARY_EXT:
        return True
    try:
        with open(p, "rb") as f:
            chunk = f.read(4096)
        if b"\0" in chunk:
            return True
        chunk.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True
    except Exception:
        return True


def _stat_entry(p: Path) -> Dict[str, Any]:
    try:
        st = p.stat()
        is_dir = p.is_dir()
        return {
            "name": p.name,
            "path": str(p),
            "type": "dir" if is_dir else "file",
            "size": 0 if is_dir else st.st_size,
            "mtime": int(st.st_mtime),
            "mode": oct(st.st_mode & 0o777),
            "symlink": p.is_symlink(),
        }
    except OSError as e:
        return {"name": p.name, "path": str(p), "type": "unknown", "error": str(e)}


def setup_routes(app: web.Application, manager=None):
    class Handlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        def _err(self, msg, status=400):
            return self._json({"error": str(msg)}, status)

        async def roots(self, req):
            try:
                items = [
                    {"path": str(r), "name": r.name or str(r),
                     "writable": os.access(r, os.W_OK)}
                    for r in _allowed_roots()
                ]
            except Exception as e:
                return self._err(e, 500)
            return self._json({"roots": items})

        async def list_dir(self, req):
            raw = req.query.get("path", "")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if not p.exists():
                return self._err(f"路径不存在: {p}", 404)
            if not p.is_dir():
                return self._err(f"不是目录: {p}", 400)

            dirs, files = [], []
            try:
                for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    if child.name in (".git", "__pycache__", "node_modules", ".venv"):
                        continue
                    e = _stat_entry(child)
                    (dirs if e["type"] == "dir" else files).append(e)
            except PermissionError:
                return self._err(f"无权限读取: {p}", 403)
            except OSError as e:
                return self._err(e, 500)

            parent = str(p.parent) if str(p) != str(p.anchor) else ""
            return self._json({
                "path": str(p), "parent": parent,
                "dirs": dirs, "files": files,
                "total": len(dirs) + len(files),
            })

        async def read_file(self, req):
            raw = req.query.get("path", "")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if not p.exists():
                return self._err(f"文件不存在: {p}", 404)
            if p.is_dir():
                return self._err("目标是目录，不能查看内容", 400)

            size = p.stat().st_size
            binary = _is_binary(p)
            meta = {"path": str(p), "name": p.name, "size": size,
                    "binary": binary, "mtime": int(p.stat().st_mtime),
                    "editable": (not binary) and size <= MAX_EDIT_BYTES}
            if binary or size > MAX_EDIT_BYTES:
                meta["content"] = ""
                meta["reason"] = ("二进制文件" if binary
                                  else f"文件过大（{size} > {MAX_EDIT_BYTES}）")
                return self._json(meta)
            try:
                meta["content"] = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return self._err(f"读取失败: {e}", 500)
            return self._json(meta)

        async def write_file(self, req):
            try:
                body = await req.json()
            except Exception as e:
                return self._err(f"invalid json: {e}")
            raw = (body or {}).get("path") or ""
            content = (body or {}).get("content")
            if content is None:
                return self._err("缺少 content")
            if not isinstance(content, str):
                return self._err("content 必须是字符串")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if p.is_dir():
                return self._err("目标是目录", 400)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                # 原子写：先写临时文件再替换，避免写一半断电
                tmp = p.with_suffix(p.suffix + ".tmp~")
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, p)
            except PermissionError:
                return self._err(f"无写入权限: {p}", 403)
            except Exception as e:
                return self._err(f"写入失败: {e}", 500)
            logger.info(f"文件已保存: {p} ({len(content)} 字符)")
            return self._json({"success": True, **_stat_entry(p)})

        async def mkdir(self, req):
            try:
                body = await req.json()
            except Exception:
                body = {}
            raw = (body or {}).get("path") or ""
            if not raw:
                return self._err("缺少 path")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if p.exists():
                return self._err(f"已存在: {p}")
            try:
                p.mkdir(parents=True)
            except Exception as e:
                return self._err(f"创建失败: {e}", 500)
            return self._json({"success": True, **_stat_entry(p)})

        async def upload(self, req):
            raw = req.query.get("path", "")
            try:
                target_dir = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if not target_dir.is_dir():
                return self._err(f"目标不是目录: {target_dir}", 400)
            reader = await req.multipart()
            saved = []
            total = 0
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name != "file":
                    continue
                fname = os.path.basename(part.filename or "upload.bin")
                if not fname or fname in (".", ".."):
                    continue
                dest = target_dir / fname
                try:
                    dest = _resolve(str(dest))       # 再校验一次（防文件名带路径）
                except PermissionError:
                    continue
                with open(dest, "wb") as f:
                    while True:
                        chunk = await part.read_chunk(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            f.close()
                            try:
                                dest.unlink()
                            except OSError:
                                pass
                            return self._err(
                                f"超出单次上传上限 {MAX_UPLOAD_BYTES // (1024*1024)}MB", 413)
                        f.write(chunk)
                saved.append(_stat_entry(dest))
            if not saved:
                return self._err("没有收到文件（字段名应为 file）")
            logger.info(f"上传完成: {[s['name'] for s in saved]} → {target_dir}")
            return self._json({"success": True, "files": saved, "bytes": total})

        async def download(self, req):
            raw = req.query.get("path", "")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if not p.is_file():
                return self._err(f"文件不存在: {p}", 404)
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return web.FileResponse(
                path=p,
                headers={
                    "Content-Type": ctype,
                    "Content-Disposition": f'attachment; filename="{p.name}"',
                    "Access-Control-Allow-Origin": "*",
                })

        async def delete(self, req):
            try:
                body = await req.json()
            except Exception:
                body = {}
            paths = (body or {}).get("paths") or []
            permanent = bool((body or {}).get("permanent"))
            if not isinstance(paths, list) or not paths:
                return self._err("缺少 paths 列表")

            trash = Path(os.environ.get("AMM_ROOT", "/amm")) / ".trash"
            done, failed = [], []
            for raw in paths:
                try:
                    p = _resolve(raw)
                except PermissionError as e:
                    failed.append({"path": raw, "error": str(e)})
                    continue
                if not p.exists():
                    failed.append({"path": raw, "error": "不存在"})
                    continue
                # 保护：不允许删允许根目录本身
                if p in _allowed_roots():
                    failed.append({"path": raw, "error": "不能删除根目录本身"})
                    continue
                try:
                    if permanent:
                        if p.is_dir():
                            shutil.rmtree(p)
                        else:
                            p.unlink()
                    else:
                        # 默认进回收站，可恢复
                        trash.mkdir(parents=True, exist_ok=True)
                        stamp = time.strftime("%Y%m%d-%H%M%S")
                        dest = trash / f"{p.name}.{stamp}"
                        shutil.move(str(p), str(dest))
                    done.append(raw)
                except Exception as e:
                    failed.append({"path": raw, "error": str(e)})
            logger.info(f"删除: 成功 {len(done)} 失败 {len(failed)} "
                        f"(permanent={permanent})")
            return self._json({
                "success": not failed, "deleted": done, "failed": failed,
                "mode": "permanent" if permanent else "trash",
                "trash_dir": str(trash) if not permanent else "",
            })

        async def rename(self, req):
            try:
                body = await req.json()
            except Exception:
                body = {}
            raw = (body or {}).get("path") or ""
            new_name = (body or {}).get("new_name") or ""
            if not raw or not new_name:
                return self._err("缺少 path 或 new_name")
            if "/" in new_name or new_name in (".", ".."):
                return self._err("new_name 不能含路径分隔符")
            try:
                p = _resolve(raw)
            except PermissionError as e:
                return self._err(e, 403)
            if not p.exists():
                return self._err("源不存在", 404)
            dest = p.parent / new_name
            try:
                dest = _resolve(str(dest))
            except PermissionError as e:
                return self._err(e, 403)
            if dest.exists():
                return self._err(f"目标已存在: {dest.name}")
            try:
                p.rename(dest)
            except Exception as e:
                return self._err(f"重命名失败: {e}", 500)
            return self._json({"success": True, **_stat_entry(dest)})

    h = Handlers()
    app.router.add_get("/api/fm/roots", h.roots)
    app.router.add_get("/api/fm/list", h.list_dir)
    app.router.add_get("/api/fm/read", h.read_file)
    app.router.add_post("/api/fm/write", h.write_file)
    app.router.add_post("/api/fm/mkdir", h.mkdir)
    app.router.add_post("/api/fm/upload", h.upload)
    app.router.add_get("/api/fm/download", h.download)
    app.router.add_post("/api/fm/delete", h.delete)
    app.router.add_post("/api/fm/rename", h.rename)
