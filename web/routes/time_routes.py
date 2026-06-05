"""时间与剧情节奏路由。"""

import logging

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

time_bp = Blueprint("time_routes", __name__, url_prefix="/time")


@time_bp.route("/")
@login_required
def index():
    ctx = _ctx()
    tm = ctx.time_manager
    return render_template(
        "time.html",
        world_name=ctx.world.WORLD_NAME,
        day=tm.day,
        time_period=tm.time_period,
        season=tm.season,
        recent_days=tm.recent_days,
        rounds_in_period=tm.rounds_in_current_period,
        ctx=ctx,
    )


@time_bp.route("/save", methods=["POST"])
@login_required
def save():
    ctx = _ctx()
    tm = ctx.time_manager
    action = request.form.get("action", "save")

    if action == "advance_period":
        tm.advance_period()
        audit_log("编辑时间", f"推进时段 → {tm.time_period}")
        return _flash_redirect(url_for("time_routes.index"),
                               f"时段推进 → 第{tm.day}天 · {tm.time_period}")
    elif action == "advance_day":
        tm.advance_day()
        audit_log("编辑时间", f"推进一天 → 第{tm.day}天")
        return _flash_redirect(url_for("time_routes.index"),
                               f"推进到第{tm.day}天清晨")

    # save
    try:
        tm.day = max(1, int(request.form.get("day", str(tm.day))))
    except ValueError:
        pass
    tm.time_period = request.form.get("time_period", tm.time_period)
    tm.season = request.form.get("season", tm.season)
    tm.recent_days = [
        line.strip() for line in
        (request.form.get("recent_days") or "").split("\n")
        if line.strip()
    ]
    tm.save()
    audit_log("编辑时间", f"保存: 第{tm.day}天 {tm.time_period} {tm.season}")
    logger.info("Web panel: saved time state for %s", ctx.world.WORLD_NAME)
    return _flash_redirect(url_for("time_routes.index"), "时间状态已保存")


@time_bp.route("/<int:index>/delete", methods=["POST"])
@login_required
def delete_note(index: int):
    ctx = _ctx()
    tm = ctx.time_manager
    if 0 <= index < len(tm.recent_days):
        removed = tm.recent_days.pop(index)
        tm.save()
        audit_log("编辑时间", f"删除摘要: {removed[:30]}…")
        return _flash_redirect(url_for("time_routes.index"),
                               f"已删除: {removed[:30]}…")
    return _flash_redirect(url_for("time_routes.index"), "无效索引", "error")
