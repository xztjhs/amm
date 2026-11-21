"""
Port Allocator — 动态实例端口分配器
===================================
v0.9 的 9 类固定场景在 models_config.yaml 里写死端口（18081-18089）。
v1.0 改为动态实例后，同一类型可运行多个实例，必须自动分配端口。

规则:
  - 保留段 (reserved): 9 类固定场景占用的端口，永不分配给动态实例
  - 动态池 (pool):     [start, end] 区间内按序分配，优先最小可用端口
  - 分配/释放均为进程内状态；实例端口本身会持久化到 SQLite，
    服务重启时由 ModelManager 重新 reserve，避免端口漂移
"""
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger("AMM.PortAllocator")


class PortAllocator:
    """端口池分配器（线程/协程安全的最小实现，单事件循环内使用）"""

    def __init__(self, start: int = 18100, end: int = 18499, reserved: Optional[Iterable[int]] = None):
        if start > end:
            raise ValueError(f"无效端口池范围: {start}-{end}")
        self.start = start
        self.end = end
        self.reserved: Set[int] = set(reserved or [])
        self.allocated: Set[int] = set()

    # ------------------------------------------------------------------
    def reserve(self, port: int) -> bool:
        """保留一个端口（固定场景实例 / 重启恢复）。已在池外也允许保留。"""
        try:
            p = int(port)
        except (TypeError, ValueError):
            return False
        self.reserved.add(p)
        self.allocated.discard(p)
        return True

    def allocate(self, preferred: Optional[int] = None) -> Optional[int]:
        """分配一个可用端口。

        preferred 优先（用于"用户指定端口"场景），但必须空闲且在池内。
        返回 None 表示池已耗尽。
        """
        if preferred is not None:
            try:
                p = int(preferred)
            except (TypeError, ValueError):
                p = None
            if p is not None and self.start <= p <= self.end and p not in self.reserved and p not in self.allocated:
                self.allocated.add(p)
                return p
            logger.warning(f"指定端口 {preferred} 不可用（越界或已占用），改为自动分配")

        for p in range(self.start, self.end + 1):
            if p not in self.reserved and p not in self.allocated:
                self.allocated.add(p)
                return p
        logger.error(f"动态端口池耗尽: {self.start}-{self.end}")
        return None

    def release(self, port: Optional[int]) -> None:
        """释放端口（实例停止/注销时）"""
        if port is None:
            return
        try:
            self.allocated.discard(int(port))
        except (TypeError, ValueError):
            pass

    def is_free(self, port: int) -> bool:
        try:
            p = int(port)
        except (TypeError, ValueError):
            return False
        return p not in self.reserved and p not in self.allocated

    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "pool": [self.start, self.end],
            "reserved": sorted(self.reserved),
            "allocated": sorted(self.allocated),
            "available": self.end - self.start + 1 - len(self.allocated),
        }

    def __repr__(self) -> str:
        return f"<PortAllocator {self.start}-{self.end} allocated={len(self.allocated)}>"
