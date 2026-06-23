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
    """短期记忆 + 长期记忆合并页面（含诊断信息）。

    短期记忆展示：最新消息在上方（页面倒序），但每条记录携带原始 storage_index，
    编辑/删除操作使用 storage_index 定位底层数据。
    """
    ctx = _ctx()
    all_messages = ctx.memory.memory

    # 页面倒序展示（最新在上），附加 storage_index
    display_messages = [
        {"storage_index": idx, "message": msg}
        for idx, msg in reversed(list(enumerate(all_messages)))
    ]

    # 截取最近 120 条展示
    display_messages = display_messages[:120]

    mem_status = ctx.memory.get_memory_status()

    # 回复长度参数
    reply_params = {
        "MIN_REPLY_TOKENS": settings.MIN_REPLY_TOKENS,
        "MID_REPLY_TOKENS": settings.MID_REPLY_TOKENS,
        "MAX_REPLY_TOKENS": settings.MAX_REPLY_TOKENS,
        "SPLIT_THRESHOLD": settings.SPLIT_THRESHOLD,
    }

    # 最近一次 LLM 调用
    last_call = None
    if ctx.client.router:
        history = ctx.client.router.get_call_history()
        if history:
            last_call = history[-1]

    # 空数据保护触发状态
    empty_protection_triggered = getattr(ctx.memory, '_empty_protection_triggered', False)

    # 长期记忆（过滤垃圾后的真实列表）
    real_long_items = ctx.memory._get_real_memories()
    real_long_count = len(real_long_items)

    return render_template(
        "memory.html",
        short_messages=display_messages,
        short_count=ctx.memory.message_count,
        long_items=real_long_items,
        long_count=real_long_count,
        mem_status=mem_status,
        reply_params=reply_params,
        last_call=last_call,
        empty_protection_triggered=empty_protection_triggered,
        ctx=ctx,
    )


@memory_bp.route("/short/<int:index>/edit", methods=["POST"])
@login_required
def edit_short_memory(index: int):
    """编辑单条短期记忆（按存储索引）。"""
    ctx = _ctx()
    content = (request.form.get("content") or "").strip()
    if not content:
        return _flash_redirect(url_for("memory.short_memory"), "内容不能为空", "error")
    if len(content) > settings.MEMO_SIZE_LIMIT:
        return _flash_redirect(url_for("memory.short_memory"),
                               f"内容过长，限制 {settings.MEMO_SIZE_LIMIT} 字", "error")

    role = (request.form.get("role") or "").strip()
    role = role if role in ("user", "assistant") else None

    result = ctx.memory.update_short_memory(index, content, role)
    if result is None:
        return _flash_redirect(url_for("memory.short_memory"), "目标记录不存在或索引无效", "error")

    audit_log("编辑短期记忆", f"#{index + 1}: → {content[:40]}")
    logger.info("Web panel: edited short memory #%d", index + 1)
    return _flash_redirect(url_for("memory.short_memory"), f"短期记忆 #{index + 1} 修改成功")


@memory_bp.route("/short/<int:index>/delete", methods=["POST"])
@login_required
def delete_short_memory(index: int):
    """删除单条短期记忆（按存储索引）。"""
    ctx = _ctx()
    removed = ctx.memory.delete_short_memory(index)
    if removed is None:
        return _flash_redirect(url_for("memory.short_memory"), "目标记录不存在或索引无效", "error")

    audit_log("删除短期记忆", f"#{index + 1}: {removed.get('content', '')[:60]}")
    logger.info("Web panel: deleted short memory #%d", index + 1)
    return _flash_redirect(url_for("memory.short_memory"), f"短期记忆 #{index + 1} 已删除")


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
    removed = ctx.memory.delete_long_memory_by_index(index)
    if removed:
        audit_log("删除记忆", removed[:80])
        logger.info("Web panel: deleted long memory item: %s", removed[:50])
        return _flash_redirect(url_for("memory.short_memory"), f"已删除: {removed[:60]}…")
    return _flash_redirect(url_for("memory.short_memory"), "无效索引", "error")


@memory_bp.route("/long/<int:index>/edit", methods=["POST"])
@login_required
def edit_long_memory(index: int):
    """编辑单条长期记忆。"""
    ctx = _ctx()
    new_text = (request.form.get("content") or "").strip()
    if not new_text:
        return _flash_redirect(url_for("memory.short_memory"), "内容不能为空", "error")
    if ctx.memory.edit_long_memory_by_index(index, new_text):
        audit_log("编辑记忆", f"#{index + 1}: → {new_text[:40]}")
        logger.info("Web panel: edited long memory item #%d", index + 1)
        return _flash_redirect(url_for("memory.short_memory"), f"已更新 #{index + 1}")
    return _flash_redirect(url_for("memory.short_memory"), "无效索引", "error")


@memory_bp.route("/long/cleanup", methods=["POST"])
@login_required
def cleanup_long_memory():
    """清理长期记忆中的垃圾条目（```json、[、]、``` 等）。"""
    ctx = _ctx()
    try:
        result = ctx.memory.cleanup_polluted_memories()
        audit_log("清理记忆", f"删除 {result['removed']} 条垃圾，保留 {result['kept']} 条")
        logger.info("Web panel: memory cleanup — removed %d, kept %d", result["removed"], result["kept"])
        if result["removed"] > 0:
            return _flash_redirect(
                url_for("memory.short_memory"),
                f"已清理 {result['removed']} 条垃圾记忆，保留 {result['kept']} 条真实记忆",
            )
        else:
            return _flash_redirect(url_for("memory.short_memory"), "记忆很干净，没有发现垃圾条目")
    except Exception as exc:
        logger.error("Web panel memory cleanup failed: %s", exc)
        return _flash_redirect(url_for("memory.short_memory"), f"清理失败: {exc}", "error")


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
