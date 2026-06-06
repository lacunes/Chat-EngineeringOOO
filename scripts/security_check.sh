#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 敏感信息安全检查脚本
# 用途：检查项目中是否存在泄露的敏感信息
# 使用：bash scripts/security_check.sh
# ═══════════════════════════════════════════════════════════════

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # 无颜色

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "════════════════════════════════════════════════"
echo "  敏感信息安全检查"
echo "  项目: $PROJECT_DIR"
echo "════════════════════════════════════════════════"
echo ""

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass()  { echo -e "  ${GREEN}✓${NC} $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail()  { echo -e "  ${RED}✗${NC} $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

# ── 1. 检查 .env 是否被 git 跟踪 ──
echo "── 1. Git 跟踪检查 ──"
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    fail ".env 被 git 跟踪！请立即运行: git rm --cached .env"
else
    pass ".env 未被 git 跟踪"
fi

# ── 2. 检查 secrets.json ──
if git ls-files --error-unmatch secrets.json >/dev/null 2>&1; then
    fail "secrets.json 被 git 跟踪！"
elif [ -f "secrets.json" ]; then
    pass "secrets.json 存在但未被 git 跟踪"
else
    pass "secrets.json 不存在（无风险）"
fi

# ── 3. 检查 logs/ ──
if git ls-files --error-unmatch logs/ >/dev/null 2>&1; then
    fail "logs/ 目录被 git 跟踪！"
else
    pass "logs/ 目录未被 git 跟踪"
fi

# ── 4. 检查 backups/ ──
if [ -d "backups" ] && git ls-files --error-unmatch backups/ >/dev/null 2>&1; then
    fail "backups/ 目录被 git 跟踪！"
else
    pass "backups/ 未被跟踪（或无此目录）"
fi

# ── 5. 检查 .gitignore 关键规则 ──
echo ""
echo "── 2. .gitignore 规则检查 ──"
check_gitignore() {
    local pattern="$1"
    local desc="$2"
    if grep -qF "$pattern" .gitignore 2>/dev/null; then
        pass ".gitignore 包含规则: $desc"
    else
        warn ".gitignore 缺少规则: $desc — 将自动追加"
        echo "$pattern" >> .gitignore
        pass "已自动追加: $desc → .gitignore"
    fi
}
check_gitignore ".env"           ".env"
check_gitignore "*.bak"          "*.bak (备份文件)"
check_gitignore "logs/"          "logs/ (日志目录)"
check_gitignore "__pycache__/"   "__pycache__/"
check_gitignore "*.pyc"          "*.pyc"
check_gitignore "secrets.json"   "secrets.json"
check_gitignore "web_audit.log"  "web_audit.log"
check_gitignore "*.env.backup.*" "*.env.backup.*"

# ── 6. 扫描项目文件中的疑似敏感信息 ──
echo ""
echo "── 3. 敏感信息扫描 ──"
# 定义扫描模式（只匹配疑似泄露，不打印实际内容）
PATTERNS=(
    "sk-[a-zA-Z0-9]\{20,\}"
    "[0-9]\{8,10\}:[a-zA-Z0-9_-]\{25,\}"
    "BOT_TOKEN\s*=\s*[^\s#]\+"
    "DEEPSEEK_KEY\s*=\s*[^\s#]\+"
    "WEB_PASSWORD\s*=\s*[^\s#]\+"
    "ALLOWED_ID\s*=\s*[0-9]\{5,\}"
)
# 排除目录
EXCLUDE_DIRS=".git|venv|.venv|node_modules|__pycache__|.pytest_cache|.mypy_cache|.ruff_cache|.idea|.vscode|logs|memory"

SCAN_FOUND=0
for pattern in "${PATTERNS[@]}"; do
    # 搜索 .py .env .json .txt .yaml .yml .sh 文件
    matches=$(find . -type f \
        \( -name "*.py" -o -name "*.env*" -o -name "*.json" -o -name "*.txt" \
           -o -name "*.yaml" -o -name "*.yml" -o -name "*.sh" -o -name "*.cfg" \
           -o -name "*.ini" -o -name "*.toml" \) \
        -not -path "*/.git/*" \
        -not -path "*/venv/*" \
        -not -path "*/.venv/*" \
        -not -path "*/node_modules/*" \
        -not -path "*/__pycache__/*" \
        -not -path "*/.pytest_cache/*" \
        -not -path "*/.mypy_cache/*" \
        -not -path "*/logs/*" \
        -not -path "*/memory/*" \
        2>/dev/null | head -20)

    if [ -n "$matches" ]; then
        while IFS= read -r file; do
            if [ -n "$file" ]; then
                # 找到匹配行，只输出位置不输出内容
                hits=$(grep -n "$pattern" "$file" 2>/dev/null | head -5)
                if [ -n "$hits" ]; then
                    while IFS= read -r hitline; do
                        lineno=$(echo "$hitline" | cut -d: -f1)
                        warn "疑似敏感内容: $file:$lineno"
                        SCAN_FOUND=$((SCAN_FOUND + 1))
                    done <<< "$hits"
                fi
            fi
        done <<< "$matches"
    fi
done

if [ $SCAN_FOUND -eq 0 ]; then
    pass "未发现疑似敏感信息泄露"
fi

# ── 7. 检查 .env 文件权限（仅 Linux/macOS）──
echo ""
echo "── 4. 文件权限检查 ──"
if [ -f ".env" ]; then
    if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
        perms=$(stat -c "%a" .env 2>/dev/null || stat -f "%Lp" .env 2>/dev/null || echo "unknown")
        if [ "$perms" = "600" ] || [ "$perms" = "400" ]; then
            pass ".env 权限安全: $perms"
        elif [ "$perms" != "unknown" ]; then
            warn ".env 权限 $perms，建议设为 600 (chmod 600 .env)"
        fi
    else
        pass "Windows 环境，跳过权限检查"
    fi
else
    warn ".env 文件不存在"
fi

# ── 8. 检查备份文件中的敏感信息 ──
echo ""
echo "── 5. 备份文件检查 ──"
backup_count=$(find . -name ".env.backup.*" -not -path "*/.git/*" 2>/dev/null | wc -l)
if [ "$backup_count" -gt 0 ]; then
    warn "发现 $backup_count 个 .env 备份文件，请确认它们不在 git 仓库中"
else
    pass "未发现 .env 备份文件"
fi

# ── 汇总 ──
echo ""
echo "════════════════════════════════════════════════"
echo "  检查完成"
echo "  通过: $PASS_COUNT  警告: $WARN_COUNT  失败: $FAIL_COUNT"
echo "════════════════════════════════════════════════"

if [ $FAIL_COUNT -gt 0 ]; then
    echo ""
    echo -e "${RED}发现严重安全问题！请立即处理上面标记 ✗ 的项目。${NC}"
    exit 1
elif [ $WARN_COUNT -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}发现潜在风险，建议检查上面标记 ⚠ 的项目。${NC}"
else
    echo ""
    echo -e "${GREEN}所有检查通过，未发现安全问题。${NC}"
fi
