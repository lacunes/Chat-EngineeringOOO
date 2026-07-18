"""时间与剧情节奏路由。

管理时间状态 + 剧情阶段 + 下一轮倾向（runtime_directive.json）+ 剧情状态（story_state.json）。
"""

import json
import logging
from pathlib import Path

from flask import Blueprint, render_template, request, url_for

from config import settings
from bot.story_state import StoryStateManager
from web.app import _ctx, audit_log, _flash_redirect
from web.routes.auth import login_required

logger = logging.getLogger(__name__)

time_bp = Blueprint("time_routes", __name__, url_prefix="/time")

STORY_PHASES = ["日常", "争执", "危机", "亲密", "调查", "战斗", "过渡"]
NEXT_TENDENCIES = ["平稳推进", "增加冲突", "增加暧昧", "增加悬念", "让 NPC 主动介入"]
TIME_PERIODS = ["清晨", "上午", "中午", "下午", "傍晚", "夜晚", "深夜"]
SEASONS = ["春", "夏", "秋", "冬"]
PACING_OPTIONS = ["slow", "normal", "intense"]


def _directive_path() -> Path:
    return settings.BASE_DIR / "runtime_directive.json"


def _load_directive() -> dict:
    path = _directive_path()
    if not path.exists():
        return {"story_phase": "日常", "next_tendency": "平稳推进", "enabled": False}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"story_phase": "日常", "next_tendency": "平稳推进", "enabled": False}


def _save_directive(data: dict) -> None:
    _directive_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@time_bp.route("/")
@login_required
def index():
    ctx = _ctx()
    tm = ctx.time_manager
    directive = _load_directive()

    # 读取剧情状态
    story_mgr = StoryStateManager(ctx.world.WORLD_NAME)
    story_state = story_mgr.state

    return render_template(
        "time.html",
        world_name=ctx.world.WORLD_NAME,
        day=tm.day,
        time_period=tm.time_period,
        season=tm.season,
        recent_days=tm.recent_days,
        rounds_in_period=tm.rounds_in_current_period,
        story_phases=STORY_PHASES,
        next_tendencies=NEXT_TENDENCIES,
        pacing_options=PACING_OPTIONS,
        directive=directive,
        story_state=story_state,
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
    elif action == "save_directive":
        directive = {
            "enabled": request.form.get("directive_enabled") == "true",
            "story_phase": request.form.get("story_phase", "日常"),
            "next_tendency": request.form.get("next_tendency", "平稳推进"),
        }
        _save_directive(directive)
        audit_log("编辑剧情节奏", f"阶段={directive['story_phase']}, 倾向={directive['next_tendency']}")
        return _flash_redirect(url_for("time_routes.index"), "剧情节奏指令已保存")
    elif action == "save_story_state":
        story_mgr = StoryStateManager(ctx.world.WORLD_NAME)
        updates = {
            "chapter": (request.form.get("ss_chapter") or "").strip(),
            "scene": (request.form.get("ss_scene") or "").strip(),
            "location": (request.form.get("ss_location") or "").strip(),
            "active_characters": _parse_text_list(request.form.get("ss_active_chars", "")),
            "current_conflict": (request.form.get("ss_conflict") or "").strip(),
            "current_goal": (request.form.get("ss_goal") or "").strip(),
            "pacing": request.form.get("ss_pacing", "normal"),
            "allowed_events": _parse_text_list(request.form.get("ss_allowed", "")),
            "forbidden_events": _parse_text_list(request.form.get("ss_forbidden", "")),
            "last_major_event": (request.form.get("ss_last_event") or "").strip(),
            "notes": (request.form.get("ss_notes") or "").strip(),
        }
        story_mgr.update(updates)
        audit_log("编辑剧情状态", f"章节={updates['chapter']}, 场景={updates['scene']}")
        return _flash_redirect(url_for("time_routes.index"), "剧情状态已保存")

    # save time state
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
    return _flash_redirect(url_for("time_routes.index"), "时间状态已保存")


@time_bp.route("/<int:index>/delete", methods=["POST"])
@login_required
def delete_note(index: int):
    ctx = _ctx()
    tm = ctx.time_manager
    if 0 <= index < len(tm.recent_days):
        removed = tm.recent_days.pop(index)
        tm.save()
        audit_log("编辑时间", f"删除摘要: {removed[:30]}")
        return _flash_redirect(url_for("time_routes.index"),
                               f"已删除: {removed[:30]}…")
    return _flash_redirect(url_for("time_routes.index"), "无效索引", "error")


def _parse_text_list(text: str) -> list[str]:
    """将逗号/换行分隔的文本解析为列表。"""
    if not text:
        return []
    items = []
    for line in text.replace(",", "\n").split("\n"):
        item = line.strip()
        if item:
            items.append(item)
    return items
