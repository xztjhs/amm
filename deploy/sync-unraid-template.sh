#!/usr/bin/env bash
# =============================================================================
# sync-unraid-template.sh — 让 AMM 成为 Unraid Docker 页的「标准受管容器」
# =============================================================================
# 参考同机位的 ACD / LMStudio 容器做法（dockerMan 标准容器）：
#   - 容器带 net.unraid.docker.* 标签 → Docker 页显示图标、WebUI 按钮、
#     可直接启停/编辑/重建
#   - 模板文件 /boot/config/plugins/dockerMan/templates-user/my-<Name>.xml
#
# 为什么需要这个脚本：
#   AMM 之前是手工 `docker run` 起的，**没有任何 dockerMan 标签**，
#   所以在 Unraid Docker 页里是个「孤儿」容器：没有图标、没有 WebUI 按钮、
#   不能用界面重建，改配置只能敲命令行。
#
# 用法（在 Unraid 宿主机上执行）：
#   bash deploy/sync-unraid-template.sh            # 只更新模板
#   bash deploy/sync-unraid-template.sh --recreate # 更新模板并按模板重建容器
#
# ⚠️ --recreate 会删除并重建容器（数据都在挂载卷里，不受影响），
#    但正在运行的模型实例会被中断。
# =============================================================================
set -euo pipefail

TEMPLATE_SRC="$(cd "$(dirname "$0")" && pwd)/unraid/my-AMM.xml"
TEMPLATES_DIR="/boot/config/plugins/dockerMan/templates-user"
TEMPLATE_DST="$TEMPLATES_DIR/my-AMM.xml"
NAME="AMM"

say()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

RECREATE=0
[ "${1:-}" = "--recreate" ] && RECREATE=1

[ -f "$TEMPLATE_SRC" ] || die "找不到模板源: $TEMPLATE_SRC"
command -v docker >/dev/null || die "找不到 docker"

# ---------------------------------------------------------------- 1. 更新模板
say "更新模板 -> $TEMPLATE_DST"
if [ -f "$TEMPLATE_DST" ]; then
    BAK="$TEMPLATE_DST.bak-$(date +%Y%m%d-%H%M%S)"
    cp -a "$TEMPLATE_DST" "$BAK"
    say "已备份旧模板: $BAK"
fi
install -m 644 "$TEMPLATE_SRC" "$TEMPLATE_DST"

# 校验关键字段，避免模板写坏导致 Docker 页显示错乱
say "校验模板关键字段"
WEBUI=$(grep -oP '(?<=<WebUI>).*(?=</WebUI>)' "$TEMPLATE_DST" || true)
PORTVAR=$(grep -oP 'PORT:\K[0-9]+' <<<"$WEBUI" || true)
[ -n "$PORTVAR" ] || die "模板 <WebUI> 里没有 [PORT:xxxxx]"
# WebUI 的 PORT 必须和某个 Type=Port 的 Config 值一致，否则按钮点开是错端口
if ! grep -q "Type=\"Port\".*>$PORTVAR<" "$TEMPLATE_DST"; then
    die "WebUI 端口 $PORTVAR 与任何 Type=Port 的 Config 值都不匹配（会导致 WebUI 按钮连错端口）"
fi
say "WebUI=$WEBUI  (端口 $PORTVAR 已与端口映射对齐)"

# ---------------------------------------------------------------- 2. 容器检查
if ! docker inspect "$NAME" >/dev/null 2>&1; then
    warn "容器 $NAME 不存在，模板已就绪；请在 Unraid Docker 页「Add Container」选择该模板创建"
    exit 0
fi

CUR_MANAGED=$(docker inspect "$NAME" --format '{{index .Config.Labels "net.unraid.docker.managed"}}' || true)
say "当前容器 dockerMan 标签: ${CUR_MANAGED:-（无）}"

if [ "$RECREATE" -eq 0 ]; then
    if [ -z "$CUR_MANAGED" ]; then
        cat <<TIP

模板已更新，但容器仍是无标签状态 —— 在 Unraid Docker 页里仍会显示为「孤儿」。

两种补齐方式：
  A) 界面操作（推荐，最直观）：
     1) 打开 Unraid → Docker → 找到 $NAME
     2) 点容器图标 → Remove（只删容器，卷里的数据不受影响）
     3) Docker 页底部「Add Container」→ 模板选 $NAME → Apply
        （模板里的端口/挂载/变量都会自动带出来）

  B) 命令行重建（等价于 A，自动完成）：
     bash $0 --recreate

TIP
    else
        say "容器已带 dockerMan 标签，模板已同步，无需重建。"
    fi
    exit 0
fi

# ---------------------------------------------------------------- 3. 按模板重建
[ "$RECREATE" -eq 1 ] || exit 0
say "--recreate：按模板重建容器（挂载卷不受影响）"

# 从模板解析出运行参数，保证「模板 = 实际容器」，避免两处漂移。
# Unraid 宿主机没有 python，全程用 sed/awk 解析（XML 结构是固定扁平的）。

# 取 <Tag>value</Tag> 的文本（同名只取第一个）
xmlval() {
    sed -n "s:.*<$1>\\(.*\\)</$1>.*:\\1:p" "$TEMPLATE_DST" | head -1
}

CNAME=$(xmlval Name)
HOSTN=$(echo "$CNAME" | tr '[:upper:]' '[:lower:]')
NET=$(xmlval Network);  NET=${NET:-bridge}
EXTRA=$(xmlval ExtraParams)
WEBUI=$(xmlval WebUI)
ICON=$(xmlval Icon)
IMG=$(xmlval Repository)

ARGS="--name $CNAME --hostname $HOSTN --network $NET"
[ -n "$EXTRA" ] && ARGS="$ARGS $EXTRA"
case "$ARGS" in *--restart*) ;; *) ARGS="$ARGS --restart unless-stopped";; esac

# <Config ...>value</Config> 逐行解析：取 Type/Target/Mode 属性 + 标签体
while IFS= read -r line; do
    TYPE=$(sed -n 's/.*Type="\([^"]*\)".*/\1/p' <<<"$line")
    TARGET=$(sed -n 's/.*Target="\([^"]*\)".*/\1/p' <<<"$line")
    MODE=$(sed -n 's/.*Mode="\([^"]*\)".*/\1/p' <<<"$line")
    DEF=$(sed -n 's/.*Default="\([^"]*\)".*/\1/p' <<<"$line")
    VAL=$(sed -n 's:.*<Config[^>]*>\(.*\)</Config>.*:\1:p' <<<"$line")
    [ -n "$VAL" ] || VAL="$DEF"
    case "$TYPE" in
        Port)     [ -n "$VAL" ] && ARGS="$ARGS -p ${VAL}:${TARGET}/${MODE:-tcp}" ;;
        Path)     [ -n "$VAL" ] && { if [ -n "$MODE" ]; then ARGS="$ARGS -v ${VAL}:${TARGET}:${MODE}"; else ARGS="$ARGS -v ${VAL}:${TARGET}"; fi; } ;;
        Variable) [ -n "$VAL" ] && ARGS="$ARGS -e ${TARGET}=${VAL}" ;;
    esac
done < <(grep '<Config ' "$TEMPLATE_DST")

# dockerMan 标签：Unraid Docker 页靠这三个识别「标准受管容器」
ARGS="$ARGS --label net.unraid.docker.managed=dockerman"
[ -n "$WEBUI" ] && ARGS="$ARGS --label net.unraid.docker.webui=$WEBUI"
[ -n "$ICON" ]  && ARGS="$ARGS --label net.unraid.docker.icon=$ICON"
ARGS="$ARGS $IMG"

printf '%s' "$ARGS" > /tmp/amm-run-args.$$

RUN_ARGS=$(cat /tmp/amm-run-args.$$)
rm -f /tmp/amm-run-args.$$

say "重建命令（供核对）:"
printf '  docker run -d %s\n' "$RUN_ARGS"
echo
read -r -p "确认重建？运行中的模型实例会被中断 [y/N] " ans
[[ "${ans:-}" == "y" || "${ans:-}" == "Y" ]] || { say "已取消"; exit 0; }

say "停止并移除旧容器"
docker stop "$NAME" >/dev/null 2>&1 || true
docker rm "$NAME" >/dev/null 2>&1 || true

say "创建新容器（带 dockerMan 标签）"
# shellcheck disable=SC2086
docker run -d $RUN_ARGS

# ---------------------------------------------------------------- 4. 校验结果
sleep 3
say "校验新建容器"
docker ps --filter "name=^${NAME}$" --format '  状态: {{.Status}}  镜像: {{.Image}}'
for k in net.unraid.docker.managed net.unraid.docker.webui net.unraid.docker.icon; do
    v=$(docker inspect "$NAME" --format "{{index .Config.Labels \"$k\"}}" 2>/dev/null || true)
    printf '  %-32s = %s\n' "$k" "${v:-（缺失）}"
done

HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
         "http://127.0.0.1:${PORTVAR}/api/health" 2>/dev/null || echo "000")
say "健康检查 http://127.0.0.1:${PORTVAR}/api/health -> $HEALTH"

if [ "$HEALTH" = "200" ]; then
    say "完成。Unraid Docker 页现在应显示 $NAME 的图标与 WebUI 按钮。"
else
    warn "容器已创建但健康检查未通过（$HEALTH），请查看: docker logs $NAME"
fi
