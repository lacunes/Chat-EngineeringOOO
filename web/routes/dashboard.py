"""仪表盘路由。"""

import logging
import re
import time

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log, _format_uptime, _flash_redirect
from web.routes.auth import login_required

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)

# 最近错误摘要时跳过的无意义行
_SKIP_ERROR_PATTERNS = [
    re.compile(r"No error handlers are registered"),
    re.compile(r"unhandled exception", re.I),
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

    # 最近错误摘要：跳过无意义的框架提示，展示真正的异常原因
    error_summary = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        # 从后往前找真正的 ERROR，跳过框架噪音
        for line in reversed(all_lines):
            if "ERROR" not in line and "CRITICAL" not in line:
                continue
            if any(p.search(line) for p in _SKIP_ERROR_PATTERNS):
                continue
            error_summary = line.strip()[-300:]
            break

        # 如果上面没找到，再看看有没有带 Traceback 的上下文（真正的异常通常在 ERROR 前一行）
        if not error_summary:
            for i in range(len(all_lines) - 1, -1, -1):
                if "Traceback (most recent call last)" in all_lines[i]:
                    # 取这一行和下一行作为摘要
                    excerpt = all_lines[i].strip()
                    if i + 1 < len(all_lines):
                        excerpt += " | " + all_lines[i + 1].strip()[:200]
                    error_summary = excerpt[-300:]
                    break

        # 过滤敏感信息
        if error_summary:
            import os
            for secret in [os.getenv("BOT_TOKEN", ""), os.getenv("DEEPSEEK_KEY", ""), os.getenv("WEB_PASSWORD", "")]:
                if secret and len(secret) > 4:
                    error_summary = error_summary.replace(secret, "***")
    except Exception:
        pass

    return render_template(
        "dashboard.html",
        world_name=ctx.world.WORLD_NAME,
        model=settings.MODEL_NAME,
        memory_count=ctx.memory.message_count,
        long_count=ctx.memory.long_memory_count,
        relation_count=len(ctx.relationship_manager.relations),
        time_day=ctx.time_manager.day,
        time_period=ctx.time_manager.time_period,
        npc_status=ctx.npc_manager.get_status_text(),
        uptime=uptime,
        log_size=log_size,
        error_summary=error_summary,
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
