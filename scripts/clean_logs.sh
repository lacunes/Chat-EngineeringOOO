#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# 日志清理脚本
#
# 删除 logs/ 目录下的轮转备份文件（*.log.*），
# 保留当前日志文件（*.log）。
#
# 用法：
#   bash scripts/clean_logs.sh        # 清理前确认
#   bash scripts/clean_logs.sh --yes  # 跳过确认直接执行
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS_DIR="$(cd "$SCRIPT_DIR/../logs" 2>/dev/null && pwd || true)"

if [ -z "${LOGS_DIR:-}" ] || [ ! -d "$LOGS_DIR" ]; then
    echo "错误：找不到 logs/ 目录"
    exit 1
fi

echo "=== 日志目录: $LOGS_DIR ==="
echo ""

# 列出将要删除的文件
to_delete=()
while IFS= read -r -d '' file; do
    to_delete+=("$file")
done < <(find "$LOGS_DIR" -maxdepth 1 -type f -name "*.log.*" -print0 2>/dev/null || true)

if [ ${#to_delete[@]} -eq 0 ]; then
    echo "没有找到轮转备份文件（*.log.*），无需清理。"
    echo ""
    echo "当前日志文件（保留）："
    find "$LOGS_DIR" -maxdepth 1 -type f -name "*.log" ! -name "*.log.*" -exec basename {} \; 2>/dev/null || true
    exit 0
fi

echo "以下文件将被删除："
printf "  %s\n" "$(basename "${to_delete[@]}")"
echo ""
echo "以下文件将被保留："
find "$LOGS_DIR" -maxdepth 1 -type f -name "*.log" ! -name "*.log.*" -exec basename {} \; 2>/dev/null || true
echo ""

if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
    echo "正在删除……"
    for f in "${to_delete[@]}"; do
        rm -f "$f"
    done
    echo "完成。已删除 ${#to_delete[@]} 个轮转备份文件。"
else
    read -r -p "确认删除以上 ${#to_delete[@]} 个文件？(y/N): " confirm
    case "$confirm" in
        [yY]|[yY][eE][sS])
            for f in "${to_delete[@]}"; do
                rm -f "$f"
            done
            echo "完成。已删除 ${#to_delete[@]} 个轮转备份文件。"
            ;;
        *)
            echo "已取消。"
            ;;
    esac
fi
