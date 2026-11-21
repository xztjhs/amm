"""
Instance Store — 动态模型实例持久化（SQLite）
=============================================
v0.9 的状态持久化是 logs/state.json（只存参数/引擎选择，且会被日志清理误删）。
v1.0 引入 SQLite 独立存储动态实例：

  表 instances(
    uid         TEXT PRIMARY KEY,   -- 实例唯一 id（动态实例 uid / 固定场景 model_id）
    category    TEXT,               -- 模型类别 chat/embedding/...
    port        INTEGER,
    status      TEXT,
    is_dynamic  INTEGER,            -- 1=动态实例 0=固定场景（固定场景仍以 YAML 为准）
    data        TEXT,               -- 实例完整快照 JSON
    updated_at  REAL
  )

DB 路径: $AMM_ROOT/data/amm.db（新增目录，不触碰任何现有固定路径）
"""
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AMM.InstanceStore")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    uid         TEXT PRIMARY KEY,
    category    TEXT NOT NULL DEFAULT '',
    port        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'stopped',
    is_dynamic  INTEGER NOT NULL DEFAULT 0,
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_instances_category ON instances(category);
CREATE INDEX IF NOT EXISTS idx_instances_status   ON instances(status);
"""


class InstanceStore:
    """轻量 SQLite 存储，线程内使用（事件循环中调用均为同步小操作）"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    def upsert(self, uid: str, category: str, port: int, status: str,
               is_dynamic: bool, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO instances
                       (uid, category, port, status, is_dynamic, data, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(uid) DO UPDATE SET
                         category=excluded.category,
                         port=excluded.port,
                         status=excluded.status,
                         is_dynamic=excluded.is_dynamic,
                         data=excluded.data,
                         updated_at=excluded.updated_at""",
                    (uid, category, int(port or 0), status, 1 if is_dynamic else 0,
                     payload, time.time()),
                )
                conn.commit()
            except Exception as e:
                logger.error(f"实例持久化失败 ({uid}): {e}")
            finally:
                conn.close()

    def delete(self, uid: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM instances WHERE uid = ?", (uid,))
                conn.commit()
            except Exception as e:
                logger.error(f"实例删除失败 ({uid}): {e}")
            finally:
                conn.close()

    def get(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM instances WHERE uid = ?", (uid,)).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def list_all(self, dynamic_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM instances"
        if dynamic_only:
            sql += " WHERE is_dynamic = 1"
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            data = json.loads(row["data"] or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        return {
            "uid": row["uid"],
            "category": row["category"],
            "port": row["port"],
            "status": row["status"],
            "is_dynamic": bool(row["is_dynamic"]),
            "data": data,
            "updated_at": row["updated_at"],
        }
