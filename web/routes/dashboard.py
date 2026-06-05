"""仪表盘路由。"""

import logging
import time

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log, _format_uptime, _flash_redirect
from web.routes.auth import login_required

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)


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

    # 最近错误摘要
    error_summary = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if "ERROR" in line:
                    error_summary = line.strip()[-200:]
                    break
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
