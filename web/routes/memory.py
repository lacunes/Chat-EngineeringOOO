"""记忆管理路由。"""

import asyncio
import logging

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

memory_bp = Blueprint("memory", __name__, url_prefix="/memory")


@memory_bp.route("/")
@login_required
def short_memory():
    """短期记忆 + 长期记忆合并页面。"""
    ctx = _ctx()
    recent = ctx.memory.memory[-120:]
    return render_template(
        "memory.html",
        short_messages=recent,
        short_count=ctx.memory.message_count,
        long_items=ctx.memory.long_memory,
        long_count=ctx.memory.long_memory_count,
        ctx=ctx,
    )


@memory_bp.route("/long")
@login_required
def long_memory():
    """长期记忆页面（独立访问时重定向到合并页）。"""
    return redirect(url_for("memory.short_memory"))


@memory_bp.route("/long/add", methods=["POST"])
@login_required
def add_long_memory():
    ctx = _ctx()
    content = (request.form.get("content") or "").strip()
    if not content:
        return _flash_redirect(url_for("memory.short_memory"), "内容不能为空", "error")
    if len(content) > settings.MEMO_SIZE_LIMIT:
        return _flash_redirect(url_for("memory.short_memory"),
                               f"内容过长，限制 {settings.MEMO_SIZE_LIMIT} 字", "error")
    ctx.memory.add_long_memory_item(content)
    ctx.memory.save_long_memory()
    audit_log("编辑记忆", f"新增: {content[:60]}…")
    logger.info("Web panel: added long memory item")
    return _flash_redirect(url_for("memory.short_memory"), "已写入长期记忆")


@memory_bp.route("/long/<int:index>/delete", methods=["POST"])
@login_required
def delete_long_memory(index: int):
    ctx = _ctx()
    if 0 <= index < len(ctx.memory.long_memory):
        removed = ctx.memory.long_memory.pop(index)
        ctx.memory.save_long_memory()
        audit_log("删除记忆", removed[:80])
        logger.info("Web panel: deleted long memory item: %s", removed[:50])
        return _flash_redirect(url_for("memory.short_memory"), f"已删除: {removed[:60]}…")
    return _flash_redirect(url_for("memory.short_memory"), "无效索引", "error")


@memory_bp.route("/long/refine", methods=["POST"])
@login_required
def refine_long_memory():
    ctx = _ctx()
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(
            ctx.memory.refine_long_memory(ctx.client, force=True)
        )
        loop.close()
        audit_log("精炼记忆", f"精炼后 {ctx.memory.long_memory_count} 条")
        logger.info("Web panel: long memory refined")
        return _flash_redirect(
            url_for("memory.short_memory"),
            f"精炼完成，目前 {ctx.memory.long_memory_count} 条",
        )
    except Exception as exc:
        logger.error("Web panel refine failed: %s", exc)
        return _flash_redirect(url_for("memory.short_memory"), f"精炼失败: {exc}", "error")
