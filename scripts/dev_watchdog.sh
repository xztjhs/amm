#!/usr/bin/env bash
# AMM 开发巡检脚本（确定性检查，供 30 分钟巡检任务调用）
# 用法: bash scripts/dev_watchdog.sh [--quiet]
# 退出码: 0=全部正常, 1=发现异常（输出里带 FAIL 行）
set -uo pipefail

REPO="/home/claw/amm/work/AMM"
VENV="$REPO/.venv/bin/python"
FAILED=0
OUT=""

fail() { FAILED=1; OUT+="FAIL: $1"$'\n'; }
ok()   { OUT+="OK: $1"$'\n'; }

cd "$REPO" 2>/dev/null || { echo "FAIL: 仓库目录不存在 $REPO"; exit 1; }

# 1) 分支与工作区
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
ok "branch=$BRANCH"
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "$DIRTY" != "0" ]; then
  ok "uncommitted_files=$DIRTY（仅供参考，不视为异常）"
fi

# 2) Python 语法检查（只用 py_compile，不依赖 pytest）
if [ -x "$VENV" ]; then
  PY="$VENV"
else
  PY="python3"
fi
if "$PY" -m compileall -q backend >/dev/null 2>&1; then
  ok "python_syntax"
else
  fail "python_syntax（compileall 失败，见下方详情）"
  OUT+="$("$PY" -m compileall -q backend 2>&1 | head -20)"$'\n'
fi

# 3) 回归测试（venv 内才有 pytest；失败不阻塞其它检查）
if [ -x "$VENV" ] && "$VENV" -c "import pytest" >/dev/null 2>&1; then
  PT_LOG=$(mktemp)
  # 用退出码判定（非 TTY 下 pytest -q 不输出汇总行，grep 不可靠）
  if PYTHONPATH="$REPO" timeout 300 "$VENV" -m pytest -q -p no:cacheprovider >"$PT_LOG" 2>&1; then
    ok "pytest: all passed"
  else
    RC=$?
    if [ "$RC" = "124" ]; then
      fail "pytest timeout(>300s)"
    else
      fail "pytest exit=$RC"
    fi
    OUT+="$(tail -20 "$PT_LOG")"$'\n'
  fi
  rm -f "$PT_LOG"
else
  ok "pytest_skipped（venv 或 pytest 不可用）"
fi

# 4) 开发/冒烟服务存活（60008 是部署端口，本地不强求）
LISTEN=$( (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -cE ':(60008|18899)\b' || true)
ok "listening_ports=$LISTEN（0 表示本地未起服务，属正常）"

echo "$OUT"
if [ "$FAILED" != "0" ]; then
  echo "RESULT=FAIL"
  exit 1
fi
echo "RESULT=PASS"
exit 0
