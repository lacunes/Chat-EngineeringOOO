"""仪表盘路由。"""

import logging
import re
import time

from flask import Blueprint, jsonify, render_template, session

from config import settings
from web.app import _ctx, audit_log, _format_uptime, _flash_redirect
from web.routes.auth import login_required
from flask import url_for

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)

# 最近错误摘要时跳过的无意义行
_SKIP_ERROR_PATTERNS = [
    re.compile(r"No error handlers are registered"),
    re.compile(r"unhandled exception", re.I),
]

# 网络重试类异常（降低严重程度）
_NETWORK_EXCEPTIONS = [
    "TimedOut", "Timeout", "ConnectionError", "NetworkError",
    "ConnectionResetError", "ConnectionRefusedError",
    "RetryAfter", "TooManyRequests",
]


@dashboard_bp.route("/")
@login_required
def index():
    ctx = _ctx()
    uptime = _format_uptime(time.time() - ctx.start_time)

    log_path = settings.LOG_FILE
    try:
        log_size = f"{log_path.stat().st_size / 1024:.0f} KB"
    except Exception:
        log_size = "N/A"

    # 解析最近错误
    error_info = _parse_recent_error(log_path)

    # 记忆健康状态
    memory_status = ctx.memory.get_memory_status()

    # 从 LLMRouter 读取真实模型/Provider 状态（不再使用 settings.MODEL_NAME）
    router_status = {}
    if ctx.client.router:
        router_status = ctx.client.router.get_dashboard_status()

    return render_template(
        "dashboard.html",
        world_name=ctx.world.WORLD_NAME,
        router_status=router_status,
        memory_count=ctx.memory.message_count,
        long_count=ctx.memory.long_memory_count,
        relation_count=len(ctx.relationship_manager.relations),
        time_day=ctx.time_manager.day,
        time_period=ctx.time_manager.time_period,
        npc_status=ctx.npc_manager.get_status_text(),
        uptime=uptime,
        log_size=log_size,
        error_info=error_info,
        memory_status=memory_status,
        ctx=ctx,
    )


@dashboard_bp.route("/reset", methods=["POST"])
@login_required
def reset_world():
    """重置当前世界记忆。需二次确认（前端 data-confirm）。"""
    ctx = _ctx()
    ctx.memory.reset()
    ctx.relationship_manager.reset()
    ctx.time_manager.reset()
    audit_log("重置世界记忆", f"世界: {ctx.world.WORLD_NAME}")
    logger.info("Web panel: memory + relationships + time reset for world %s", ctx.world.WORLD_NAME)
    return _flash_redirect(url_for("dashboard.index"), "当前世界记忆、关系网络和时间均已重置")


@dashboard_bp.route("/context-debug")
@login_required
def context_debug():
    """上下文选择调试面板。显示最近一次上下文选择结果。"""
    from flask import jsonify
    ctx = _ctx()
    sel = getattr(ctx, 'last_selection', None)
    if not sel:
        return jsonify({"status": "no_data", "message": "暂无上下文选择记录。发送一条聊天消息后刷新此页面。"})
    return jsonify({"status": "ok", "selection": sel})


@dashboard_bp.route("/health")
def health():
    """轻量健康检查。不请求 Telegram 或模型 API。"""
    ctx = _ctx()
    now = time.time()
    minimal = {
        "ok": True,
        "process_alive": True,
        "web_alive": True,
        "uptime_seconds": int(now - ctx.start_time),
    }
    if not session.get("logged_in"):
        return jsonify(minimal)

    router_status = ctx.client.router.get_dashboard_status() if ctx.client.router else {}
    roleplay_bot = ctx.roleplay_bot
    return jsonify({
        **minimal,
        "telegram_polling_started": bool(getattr(ctx, "telegram_polling_started", False)),
        "active_world": getattr(ctx.world, "WORLD_NAME", None),
        "current_provider": router_status.get("current_provider"),
        "last_update_at": getattr(roleplay_bot, "last_update_at", None),
        "last_reply_at": getattr(roleplay_bot, "last_reply_at", None),
        "consecutive_telegram_network_errors": getattr(roleplay_bot, "consecutive_telegram_network_errors", 0),
    })


# ═══════════════════════════════════════════════════════════
# 错误日志解析
# ═══════════════════════════════════════════════════════════

def _parse_recent_error(log_path) -> dict | None:
    """从日志文件解析最近一次错误，返回结构化信息。

    优先解析 traceback：取最后一行（异常类型+消息）作为摘要，
    取倒数第二帧作为发生位置。网络重试类异常降低严重程度。
    """
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except Exception:
        return None

    if not all_lines:
        return None

    # ── 第一步：找到最近的 traceback 块 ──
    tb_block = _find_last_traceback_block(all_lines)

    if tb_block:
        return _parse_traceback_block(tb_block, all_lines)

    # ── 第二步：没有 traceback，找最近的 ERROR/CRITICAL 行 ──
    for line in reversed(all_lines):
        stripped = line.strip()
        if ("ERROR" not in stripped and "CRITICAL" not in stripped):
            continue
        if any(p.search(stripped) for p in _SKIP_ERROR_PATTERNS):
            continue
        return {
            "exception_type": "",
            "exception_message": _filter_sensitive(stripped[-200:]),
            "location": "",
            "full_traceback": _filter_sensitive(stripped),
            "is_network": False,
        }

    return None


def _find_last_traceback_block(lines: list[str]) -> list[str] | None:
    """找到日志中最后一个 traceback 块并返回其行列表。"""
    # 从后往前找 "Traceback (most recent call last):"
    tb_start = None
    for i in range(len(lines) - 1, -1, -1):
        if "Traceback (most recent call last):" in lines[i]:
            tb_start = i
            break

    if tb_start is None:
        return None

    # 收集从 tb_start 到块末尾的所有行
    # traceback 块以一行非缩进的行结束（通常是异常类型行），
    # 或者遇到空行 / 新的日志时间戳
    block: list[str] = []
    for i in range(tb_start, len(lines)):
        line = lines[i]
        # 遇到空行且已经收集了内容 → 块结束
        if line.strip() == "" and len(block) > 1:
            break
        # 遇到新的日志时间戳行（仿照 logging 格式: YYYY-MM-DD HH:MM:SS）且不在 traceback 中
        if _is_new_log_entry(line) and i > tb_start and not line.startswith("  ") and "Traceback" not in line:
            # 检查是否是 traceback 延续行（缩进或 File 行）
            if not (line.startswith("  ") or line.strip().startswith("File ")):
                break
        block.append(line)

    return block if len(block) >= 2 else None


def _is_new_log_entry(line: str) -> bool:
    """判断是否是新的日志条目开头（例如: 2025-01-15 14:30:05 - INFO - ...）。"""
    return bool(re.match(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+', line))


def _parse_traceback_block(block: list[str], all_lines: list[str]) -> dict | None:
    """解析 traceback 块，提取异常类型、消息、位置。"""
    if not block:
        return None

    # ── 提取异常类型和消息（traceback 最后一行）──
    last_line = block[-1].strip()
    exc_type = ""
    exc_message = ""
    location = ""
    is_network = False

    # 格式: module.ExceptionType: message
    # 或者直接: ExceptionType: message
    exc_match = re.match(r'^([\w.]+(?:Error|Exception|Warning|Timeout|Interrupt|Exit|[A-Z]\w+))(?::\s*(.*))?$', last_line)
    if exc_match:
        exc_type = exc_match.group(1)
        exc_message = exc_match.group(2) or ""
    else:
        # 回退：直接把最后一行作为消息
        exc_message = last_line[-200:]

    # ── 提取发生位置（traceback 倒数第二帧的文件行）──
    for line in reversed(block):
        file_match = re.match(r'\s*File\s+"([^"]+)",\s*line\s+(\d+)', line)
        if file_match:
            filepath = file_match.group(1)
            lineno = file_match.group(2)
            # 取文件名而非完整路径
            filename = filepath.replace("\\", "/").split("/")[-1]
            location = f"{filename}:{lineno}"
            break

    # ── 网络异常判断 ──
    if any(ne in exc_type for ne in _NETWORK_EXCEPTIONS):
        is_network = True

    # ── 构建完整 traceback 文本 ──
    full_tb = "".join(block)

    # 过滤敏感信息
    full_tb = _filter_sensitive(full_tb)
    exc_message = _filter_sensitive(exc_message)
    location = _filter_sensitive(location)

    return {
        "exception_type": exc_type,
        "exception_message": exc_message,
        "location": location,
        "full_traceback": full_tb,
        "is_network": is_network,
    }


def _filter_sensitive(text: str) -> str:
    """过滤敏感信息（委托给统一工具函数）。"""
    from bot.utils import filter_sensitive
    return filter_sensitive(text)
