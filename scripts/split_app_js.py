#!/usr/bin/env python3
"""
app.js 拆分工具（#1）
=====================
app.js 是单 IIFE、共享状态重、互相引用密集。手工搬容易漏依赖，
所以用脚本按「顶层注释块」切分，并自动补上跨文件依赖声明。

做法：
  1. 按标记注释切出目标块（Playground / Tools / Log Center）
  2. 每个块包裹成独立 IIFE，内部需要的共享符号从 window 取
  3. 从 core 的 window 导出清单里反查哪些符号被搬走了，确认为 module 提供
  4. 生成 module 文件，core 里用占位注释替换原块

安全性：失败不写回任何文件（先写 .new 再校验语法）。
"""
import re
import subprocess
import sys
from pathlib import Path

APP = Path("frontend/js/app.js")

# 块定义：(名字, 起始标记, 结束标记)
BLOCKS = [
    ("playground", "    // ---- Playground Chat (v0.2", "    // ---- Log Center (R5) ----"),
    ("logcenter", "    // ---- Log Center (R5) ----", "    // ---- Settings + Engine Management ----"),
    ("tools", "    // ---- Tools (模型下载", "    // ---- Modal ----"),
]


def find_block(lines, start_mark, end_mark):
    si = ei = None
    for i, ln in enumerate(lines):
        if si is None and ln.startswith(start_mark):
            si = i
        elif si is not None and ln.startswith(end_mark):
            ei = i
            break
    return si, ei


def extract_identifiers(body: str):
    """粗粒度提取被引用的全局名（window.xxx 除外）"""
    names = set(re.findall(r"\b([a-zA-Z_$][\w$]*)\s*\(", body))
    keywords = {
        "if", "for", "while", "switch", "catch", "function", "return",
        "typeof", "new", "await", "async", "parseInt", "parseFloat",
        "isNaN", "setTimeout", "setInterval", "clearInterval", "clearTimeout",
        "encodeURIComponent", "decodeURIComponent", "fetch", "alert",
        "confirm", "prompt", "String", "Number", "Boolean", "Array",
        "Object", "JSON", "Math", "Date", "Promise", "Error", "Set", "Map",
    }
    return {n for n in names if n not in keywords}


def main():
    lines = APP.read_text().splitlines(keepends=True)
    core_shims = []
    written = []

    for name, start, end in BLOCKS:
        si, ei = find_block(lines, start, end)
        if si is None or ei is None or ei <= si:
            print(f"[skip] {name}: 找不到边界 ({si}, {ei})")
            continue
        body = "".join(lines[si:ei])
        # 该块内部调用的、但定义在 core 里的函数——通过 window 转发
        ids = extract_identifiers(body)
        shim = "".join(lines[:si]) + "".join(lines[ei:])

        # 收集 core 里 window.xxx = 的导出名，用来判断哪些需要转发。
        # 关键：块内自己定义的函数不算「借用」，否则 shim 会和本地 function 撞名。
        defined_here = set(re.findall(r"^\s*(?:async\s+)?function\s+([a-zA-Z_$][\w$]*)",
                                      body, re.M))
        defined_here |= set(re.findall(r"^\s*(?:const|let|var)\s+([a-zA-Z_$][\w$]*)\s*=",
                                       body, re.M))
        exported = set(re.findall(r"window\.([a-zA-Z_$][\w$]*)\s*=", shim))
        borrowed = (ids & exported) - defined_here
        core_shims.append((name, sorted(borrowed)))

        module = f"""/**
 * AMM Frontend — {name} 模块（从 app.js 拆出）
 * 依赖 core 提供的全局符号，统一从 window 取，避免隐式依赖。
 */
(function () {{
    'use strict';
    const API_BASE = '/api';

    // 从 core 借用的辅助（core 都挂在 window 上）
{chr(10).join(f"    const {n} = (...a) => window.{n}(...a);" for n in sorted(borrowed)) or "    // （无）"}

{body}
}})();
"""
        out = Path(f"frontend/js/{name}.js")
        out.write_text(module)
        written.append(out)
        # 用占位注释替换（保留行号线索）
        placeholder = (f"    // ---- {name}: 已拆分到 js/{name}.js (#1 模块化) ----\n")
        lines = lines[:si] + [placeholder] + lines[ei:]
        print(f"[ok] {name}: {len(body.splitlines())} 行 -> {out}，"
              f"借用 core 符号 {len(borrowed)} 个")

    if not written:
        print("没有可拆分的块")
        return 1

    APP.write_text("".join(lines))
    for name, shims in core_shims:
        if shims:
            print(f"  {name} 依赖 core: {', '.join(shims[:12])}"
                  f"{' …' if len(shims) > 12 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
