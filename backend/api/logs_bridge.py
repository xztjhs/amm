"""
Log Center Bridge — 日志中心（R5 一期）
======================================
参考 Xinference /log-center，把分散的日志统一到一个入口：

  - 服务日志:   $LOGS_DIR/amm_server.log
  - 实例日志:   $LOGS_DIR/<model_id>_server.log

能力:
  - 聚合查看（多来源合并、按时间排序）
  - 按来源 / 级别 / 关键字 / 行数过滤
  - 下载原始日志

日志格式兼容两种：
  1) logging 标准格式   "2026-11-14 12:00:00,123 [INFO] name: msg"
  2) 子进程原始输出     任意文本（无法解析时 level 记为 RAW）
"""
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

logger = logging.getLogger("AMM.LogCenter")

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d{1,6})?)")
_LEVEL_RE = re.compile(r"\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\]", re.IGNORECASE)
_LEVELS = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL", "RAW"}

# llama.cpp / llama-server 的日志格式：
#   0.04.768.238 I srv  server_run_i: model loaded
#   （相对时间 + 单字母级别 + 模块名 + 消息）
_LLAMA_RE = re.compile(
    r"^(?P<ts>\d+(?:\.\d+){2,3})\s+(?P<lv>[IWE])\s+(?P<mod>[A-Za-z_][A-Za-z0-9_]*:?)\s*(?P<msg>.*)$")
_LLAMA_LEVEL = {"I": "INFO", "W": "WARNING", "E": "ERROR"}


def _ts_sort_key(e: Dict[str, Any]):
    """按真实时间排序。

    日志里混着两种时间戳：
      2026-11-15 12:07:45.989      （绝对时间）
      211.00.582.352               （llama 相对秒）
    直接按字符串比较会把 "211.00..." 排在 "0.03..." 前面，导致尾部截断取错行。
    """
    ts = (e.get("ts") or "").strip()
    if not ts:
        # 无时间戳的（RAW）永远排最后
        return (float("inf"), "\uffff")
    # llama 相对时间：a.bb.ccc.ddd -> 转成秒数
    if re.fullmatch(r"\d+(?:\.\d+){2,3}", ts):
        parts = ts.split(".")
        try:
            secs = float(parts[0])
            if len(parts) >= 3:
                secs += float(parts[1]) / 1000.0
                secs += float(parts[2]) / 1_000_000.0
            return (secs, "")
        except ValueError:
            return (0.0, ts)
    # 绝对时间：统一格式后按字典序即可
    return (float("inf"), ts)


def _parse_line(line: str, source: str) -> Dict[str, Any]:
    """把一行日志规范化为 {ts, level, source, message}"""
    ts = ""
    level = "RAW"
    message = line

    # llama.cpp 风格优先（时间戳不在首位时也能识别）
    lm2 = _LLAMA_RE.match(line.strip())
    if lm2:
        _mod = lm2.group("mod").rstrip(":")
        _msg = lm2.group("msg")
        return {
            "ts": lm2.group("ts"),
            "level": _LLAMA_LEVEL.get(lm2.group("lv"), "RAW"),
            "source": source,
            "message": f"{_mod}: {_msg}".strip(),
        }

    m = _TS_RE.match(line)
    if m:
        ts = m.group(1).replace(",", ".")
        rest = line[m.end():].lstrip()
    else:
        rest = line

    lm = _LEVEL_RE.search(rest[:40])
    if lm:
        level = lm.group(1).upper()
        if level == "WARN":
            level = "WARNING"
        # 去掉时间戳与级别标记，保留正文
        message = rest[lm.end():].lstrip(" :")
    elif m:
        message = rest

    return {"ts": ts, "level": level, "source": source, "message": message}


def _list_log_files(logs_dir: str) -> List[Dict[str, Any]]:
    files = []
    base = Path(logs_dir)
    if not base.exists():
        return files
    for p in sorted(base.glob("*.log")):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append({
            "name": p.name,
            "source": "server" if p.name == "amm_server.log" else p.name.replace("_server.log", ""),
            "size_bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return files


def _read_tail(path: Path, max_lines: int) -> List[str]:
    """只读文件尾部，避免大日志文件拖慢接口"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # 估算: 每行约 200 字节，多读一些保证够数
            block = min(size, max(64 * 1024, max_lines * 256))
            f.seek(size - block)
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        return lines[-max_lines:] if len(lines) > max_lines else lines
    except OSError:
        return []


def _resolve_sources(logs_dir: str, source: str) -> List[tuple]:
    """返回 [(source_name, path)]"""
    base = Path(logs_dir)
    if source and source != "all":
        fname = "amm_server.log" if source == "server" else f"{source}_server.log"
        p = base / fname
        return [("server" if source == "server" else source, p)] if p.is_file() else []
    result = []
    server_log = base / "amm_server.log"
    if server_log.is_file():
        result.append(("server", server_log))
    if base.is_dir():
        for p in sorted(base.glob("*_server.log")):
            name = p.name[:-len("_server.log")]
            if p.name == "amm_server.log":
                continue
            result.append((name, p))
    return result


def setup_routes(app: web.Application, manager):
    # 优先用运行期 LOGS_DIR（模型管理器实际写入路径），避免读到配置里的固定 /amm/logs
    try:
        from backend.core.model_manager import LOGS_DIR as _RUNTIME_LOGS_DIR
    except Exception:
        _RUNTIME_LOGS_DIR = ""
    logs_dir = _RUNTIME_LOGS_DIR or str(
        Path(manager.config.get("server", {}).get("logs_dir")
             or os.environ.get("AMM_ROOT", "/amm") + "/logs")
    )

    class LogCenterHandlers:
        def _json(self, data, status=200):
            return web.json_response(data, status=status,
                                     headers={"Access-Control-Allow-Origin": "*"})

        async def sources(self, req):
            """GET /api/logs/center/sources  日志来源列表"""
            return self._json({
                "logs_dir": logs_dir,
                "server_log": _list_log_files(logs_dir),
                "instances": sorted({
                    i.model_id for i in manager.get_all_instances().values()
                }),
            })

        async def query(self, req):
            """GET /api/logs/center?source=all&level=ALL&q=&lines=200"""
            source = (req.query.get("source") or "all").strip()
            level = (req.query.get("level") or "ALL").strip().upper()
            keyword = (req.query.get("q") or "").strip()
            try:
                lines = max(10, min(5000, int(req.query.get("lines", 200))))
            except (TypeError, ValueError):
                lines = 200

            entries: List[Dict[str, Any]] = []
            for src_name, path in _resolve_sources(logs_dir, source):
                for raw in _read_tail(path, lines):
                    if not raw.strip():
                        continue
                    entry = _parse_line(raw, src_name)
                    if level != "ALL" and entry["level"] != level:
                        continue
                    if keyword and keyword.lower() not in raw.lower():
                        continue
                    entries.append(entry)

            # 有时间戳的按时间排序，无时间戳的（RAW）保持在末尾
            timed = [e for e in entries if e["ts"]]
            untimed = [e for e in entries if not e["ts"]]
            timed.sort(key=_ts_sort_key)
            merged = (timed + untimed)[-lines:]

            level_counts = {lv: 0 for lv in sorted(_LEVELS)}
            for e in merged:
                level_counts[e["level"]] = level_counts.get(e["level"], 0) + 1

            return self._json({
                "source": source,
                "level": level,
                "keyword": keyword,
                "total": len(merged),
                "level_counts": level_counts,
                "entries": merged,
            })

        async def export_filtered(self, req):
            """GET /api/logs/center/export

            把当前筛选条件（来源/级别/关键字）命中的日志导出为文本，
            用于把有价值的历史留下来，而不是整文件下载。
            """
            source = (req.query.get("source") or "all").strip()
            level = (req.query.get("level") or "ALL").strip().upper()
            keyword = (req.query.get("q") or "").strip()
            try:
                lines = max(10, min(20000, int(req.query.get("lines") or 2000)))
            except (TypeError, ValueError):
                lines = 2000

            out = []
            for src_name, path in _resolve_sources(logs_dir, source):
                for raw in _read_tail(path, lines):
                    if not raw.strip():
                        continue
                    e = _parse_line(raw, src_name)
                    if level != "ALL" and e["level"] != level:
                        continue
                    if keyword and keyword.lower() not in raw.lower():
                        continue
                    out.append(raw)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            body = "\n".join(out) or "(no matching lines)"
            return web.Response(
                text=body,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Content-Disposition": f'attachment; filename="amm-logs-{stamp}.log"',
                    "Access-Control-Allow-Origin": "*",
                })

        async def files(self, req):
            """GET /api/logs/center/files —— 日志文件清单（大小/时间）"""
            rows = []
            for p in sorted(Path(logs_dir).glob("*.log")):
                try:
                    st = p.stat()
                except OSError:
                    continue
                rows.append({
                    "name": p.name,
                    "source": p.name[:-4] if p.name.endswith(".log") else p.name,
                    "size_mb": round(st.st_size / 1048576, 2),
                    "mtime": st.st_mtime,
                })
            rows.sort(key=lambda r: r["mtime"], reverse=True)
            total = round(sum(r["size_mb"] for r in rows), 2)
            return self._json({"files": rows, "total_mb": total, "count": len(rows)})

        async def cleanup(self, req):
            """POST /api/logs/center/cleanup

            body: {mode: "truncate"|"delete", source?: "xxx"|"all", older_than_days?: N}
            - truncate: 清空内容但保留文件（服务正在写的日志用这个）
            - delete:   删除文件
            """
            try:
                body = await req.json()
            except Exception:
                body = {}
            body = body if isinstance(body, dict) else {}
            mode = (body.get("mode") or "truncate").lower()
            source = (body.get("source") or "all").strip()
            days = body.get("older_than_days")

            targets = []
            base = Path(logs_dir)
            if source == "all":
                targets = list(base.glob("*.log"))
            else:
                for name, path in _resolve_sources(logs_dir, source):
                    targets.append(path)

            if days:
                try:
                    cutoff = time.time() - float(days) * 86400
                    targets = [p for p in targets
                               if p.exists() and p.stat().st_mtime < cutoff]
                except (TypeError, ValueError):
                    pass

            done, freed = [], 0
            for p in targets:
                try:
                    sz = p.stat().st_size
                except OSError:
                    continue
                try:
                    if mode == "delete":
                        p.unlink()
                    else:
                        with open(p, "w", encoding="utf-8"):
                            pass
                    freed += sz
                    done.append(p.name)
                except OSError as e:
                    logger.warning(f"清理日志失败 {p}: {e}")
            return self._json({
                "success": True, "mode": mode,
                "files": done, "count": len(done),
                "freed_mb": round(freed / 1048576, 2),
            })

        async def download(self, req):
            """GET /api/logs/center/download?source=server|<model_id>"""
            source = (req.query.get("source") or "").strip()
            if not source:
                return self._json({"error": "缺少 source"}, 400)
            sources = _resolve_sources(logs_dir, source)
            if not sources:
                return self._json({"error": f"日志不存在: {source}"}, 404)
            _, path = sources[0]
            return web.FileResponse(path, headers={
                "Content-Disposition": f'attachment; filename="{path.name}"'
            })

    h = LogCenterHandlers()
    app.router.add_get("/api/logs/center/sources", h.sources)
    app.router.add_get("/api/logs/center", h.query)
    app.router.add_get("/api/logs/center/download", h.download)
    app.router.add_get("/api/logs/center/export", h.export_filtered)
    app.router.add_get("/api/logs/center/files", h.files)
    app.router.add_post("/api/logs/center/cleanup", h.cleanup)
