"""模型供应商管理路由 — Web 管理面板。"""

import logging

from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from config import settings
from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

providers_bp = Blueprint("providers", __name__, url_prefix="/providers")


def _get_router():
    """获取 LLMRouter 实例。"""
    ctx = _ctx()
    return ctx.client.router


@providers_bp.route("/")
@login_required
def index():
    """模型管理主页。"""
    ctx = _ctx()
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("dashboard.index"), "LLM Router 未初始化", "error")

    providers = router.get_provider_list()
    mode = router._state.get("mode", "auto")
    manual_provider = router._state.get("manual_provider")
    last_fallback_time = router._state.get("last_fallback_time")
    last_fallback_reason = router._state.get("last_fallback_reason")

    # 回复长度参数
    reply_params = {
        "MIN_REPLY_TOKENS": settings.MIN_REPLY_TOKENS,
        "MID_REPLY_TOKENS": settings.MID_REPLY_TOKENS,
        "MAX_REPLY_TOKENS": settings.MAX_REPLY_TOKENS,
        "SPLIT_THRESHOLD": settings.SPLIT_THRESHOLD,
    }

    call_history = router.get_call_history()

    return render_template("providers.html",
                           ctx=ctx,
                           providers=providers,
                           mode=mode,
                           manual_provider=manual_provider,
                           last_fallback_time=last_fallback_time,
                           last_fallback_reason=last_fallback_reason,
                           reply_params=reply_params,
                           call_history=call_history)


@providers_bp.route("/mode", methods=["POST"])
@login_required
def set_mode():
    """切换 auto/manual 模式。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    new_mode = (request.form.get("mode") or "").strip()
    if new_mode == "auto":
        router.set_mode_auto()
        audit_log("模型管理", "切换为自动模式")
    elif new_mode == "manual":
        router.set_mode_manual("")
        audit_log("模型管理", "切换为手动模式")
    else:
        return _flash_redirect(url_for("providers.index"), f"未知模式: {new_mode}", "error")

    return _flash_redirect(url_for("providers.index"), f"已切换至 {'自动' if new_mode == 'auto' else '手动'} 模式")


@providers_bp.route("/manual", methods=["POST"])
@login_required
def set_manual_provider():
    """选择手动模式下的优先 provider。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("provider") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "未指定 provider", "error")

    router.set_mode_manual(name)
    audit_log("模型管理", f"手动优先: {name}")
    return _flash_redirect(url_for("providers.index"), f"已手动优先使用 {name}，立即生效")


@providers_bp.route("/toggle", methods=["POST"])
@login_required
def toggle_provider():
    """启用/禁用某个 provider。name 从 POST body 获取。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("name") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "未指定 provider 名称", "error")

    providers = router.get_provider_list()
    target = next((p for p in providers if p["name"] == name), None)
    if not target:
        return _flash_redirect(url_for("providers.index"), f"Provider '{name}' 不存在", "error")

    if target["enabled"]:
        ok = router.disable_provider(name)
        audit_log("模型管理", f"禁用 provider: {name}", ok)
        if not ok:
            logger.warning("Failed to disable provider: %s", name)
            return _flash_redirect(url_for("providers.index"), f"禁用 {name} 失败", "error")
        return _flash_redirect(url_for("providers.index"), f"已禁用 {name}")
    else:
        ok = router.enable_provider(name)
        audit_log("模型管理", f"启用 provider: {name}", ok)
        if not ok:
            logger.warning("Failed to enable provider: %s", name)
            return _flash_redirect(url_for("providers.index"), f"启用 {name} 失败", "error")
        return _flash_redirect(url_for("providers.index"), f"已启用 {name}")


@providers_bp.route("/clear", methods=["POST"])
@login_required
def clear_provider():
    """清除 provider 的 failures/cooldown/exhausted 状态。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("name") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "未指定 provider 名称", "error")

    ok = router.clear_provider_state(name)
    if ok:
        audit_log("模型管理", f"清除状态: {name}")
        return _flash_redirect(url_for("providers.index"), f"已清除 {name} 的失败/冷却/耗尽状态")
    else:
        logger.warning("Failed to clear provider state: %s", name)
        return _flash_redirect(url_for("providers.index"), f"Provider '{name}' 不存在", "error")


@providers_bp.route("/test", methods=["POST"])
@login_required
def test_provider():
    """测试 provider 连接。返回 JSON。"""
    router = _get_router()
    if not router:
        return jsonify({"ok": False, "error": "LLM Router 未初始化"})

    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "未指定 provider 名称"})

    result = router.test_connection(name)
    audit_log("模型管理", f"测试连接: {name} — {'成功' if result['ok'] else '失败'}")
    if not result["ok"]:
        logger.warning("Provider test failed: %s — %s", name, result.get("error", ""))
    return jsonify(result)


@providers_bp.route("/fetch-models", methods=["POST"])
@login_required
def fetch_models():
    """从 base_url + api_key 获取模型列表（/v1/models）。"""
    router = _get_router()
    if not router:
        return jsonify({"ok": False, "models": [], "error": "LLM Router 未初始化"})

    base_url = (request.form.get("base_url") or "").strip()
    api_key = (request.form.get("api_key") or "").strip()

    if not base_url:
        return jsonify({"ok": False, "models": [], "error": "base_url 不能为空"})
    if not api_key:
        return jsonify({"ok": False, "models": [], "error": "API Key 不能为空"})

    result = router.fetch_models_from_api(base_url, api_key)
    audit_log("模型管理", f"获取模型列表: {base_url[:60]} — {'成功' if result['ok'] else '失败'}")
    return jsonify(result)


@providers_bp.route("/add", methods=["POST"])
@login_required
def add_provider():
    """添加一个新的 provider。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("name") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "名称不能为空", "error")

    # 收集所有字段
    provider_config = {
        "name": name,
        "model": (request.form.get("model") or "").strip(),
        "base_url": (request.form.get("base_url") or "").strip(),
        "api_key_env": (request.form.get("api_key_env") or "").strip(),
        "priority": (request.form.get("priority") or "99").strip(),
        "enabled": request.form.get("enabled") == "true",
        "task_types": request.form.getlist("task_types") or ["chat"],
        "timeout_chat_seconds": (request.form.get("timeout_chat_seconds") or "60").strip(),
        "timeout_background_seconds": (request.form.get("timeout_background_seconds") or "30").strip(),
        "max_retries": (request.form.get("max_retries") or "1").strip(),
        "cooldown_seconds": (request.form.get("cooldown_seconds") or "300").strip(),
        "max_consecutive_failures": (request.form.get("max_consecutive_failures") or "3").strip(),
        "disable_on_quota_exhausted": request.form.get("disable_on_quota_exhausted") == "true",
        "thinking_enabled": request.form.get("thinking_enabled") == "true",
    }

    ok = router.add_provider(provider_config)
    if ok:
        audit_log("模型管理", f"添加 provider: {name}")
        return _flash_redirect(url_for("providers.index"), f"已添加 provider: {name}")
    else:
        return _flash_redirect(url_for("providers.index"), f"添加失败，可能名称 '{name}' 已存在", "error")


@providers_bp.route("/edit", methods=["POST"])
@login_required
def edit_provider():
    """编辑一个 provider 的字段。name 从 POST body 获取。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("name") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "未指定 provider 名称", "error")

    updates = {}

    # 收集所有可编辑字段（只包含表单中实际提交的）
    for field in ["model", "base_url", "api_key_env", "priority",
                  "timeout_chat_seconds", "timeout_background_seconds",
                  "max_retries", "cooldown_seconds", "max_consecutive_failures"]:
        val = request.form.get(field)
        if val is not None:
            updates[field] = val.strip()

    # task_types 是多选
    if "task_types" in request.form:
        updates["task_types"] = request.form.getlist("task_types")

    # 布尔字段
    for bool_field in ["enabled", "disable_on_quota_exhausted", "thinking_enabled"]:
        if bool_field in request.form:
            updates[bool_field] = request.form.get(bool_field) == "true"

    if not updates:
        return _flash_redirect(url_for("providers.index"), "没有需要更新的字段", "info")

    ok = router.edit_provider(name, updates)
    if ok:
        audit_log("模型管理", f"编辑 provider: {name} — {list(updates.keys())}")
        return _flash_redirect(url_for("providers.index"), f"已更新 {name}")
    else:
        logger.warning("Failed to edit provider: %s", name)
        return _flash_redirect(url_for("providers.index"), f"更新 {name} 失败", "error")


@providers_bp.route("/delete", methods=["POST"])
@login_required
def delete_provider():
    """删除一个 provider。name 从 POST body 获取。"""
    router = _get_router()
    if not router:
        return _flash_redirect(url_for("providers.index"), "LLM Router 未初始化", "error")

    name = (request.form.get("name") or "").strip()
    if not name:
        return _flash_redirect(url_for("providers.index"), "未指定 provider 名称", "error")

    ok = router.delete_provider(name)
    if ok:
        audit_log("模型管理", f"删除 provider: {name}")
        return _flash_redirect(url_for("providers.index"), f"已删除 {name}")
    else:
        logger.warning("Failed to delete provider: %s", name)
        return _flash_redirect(url_for("providers.index"), f"删除 {name} 失败", "error")


@providers_bp.route("/prompt-preview")
@login_required
def prompt_preview():
    """预览当前世界的实际 Prompt。"""
    ctx = _ctx()
    world = ctx.world_manager.get_world()
    rpb = ctx.roleplay_bot

    # 构建预览内容
    from config import prompts

    sections = []

    # 1. 固定层
    world_prompt = world.SYSTEM_PROMPT + "\n" + prompts.TIME_INJECT_INSTRUCTION
    sections.append({"title": "固定层（System Prompt + 时间指令）", "content": world_prompt})

    # 2. 半固定层
    if rpb.memory.long_memory:
        recent = rpb.memory.long_memory[-settings.LONG_MEMORY_CONTEXT_LIMIT:]
        lt_text = "[长期记忆]\n" + "\n".join(recent)
        sections.append({"title": "半固定层（长期记忆）", "content": lt_text})
    else:
        sections.append({"title": "半固定层（长期记忆）", "content": "（暂无长期记忆）"})

    # 3. 动态层
    dynamic_parts = []
    stage_directions = rpb.npc_manager.get_stage_directions("", forbidden_events=[])
    if stage_directions:
        dynamic_parts.append(prompts.NPC_STAGE_DIRECTION_INSTRUCTION + "\n" + stage_directions)

    relation_summary = rpb.relationship_manager.get_summary()
    if relation_summary:
        dynamic_parts.append(prompts.RELATION_INJECT_INSTRUCTION + relation_summary)

    time_summary = rpb.time_manager.get_summary()
    dynamic_parts.append(time_summary)

    story_summary = rpb.story_state.get_summary()
    if story_summary:
        dynamic_parts.append(story_summary)

    # 导演指令
    from bot.telegram_handlers import _load_runtime_directive, _build_directive_prompt
    directive = _load_runtime_directive()
    if directive.get("enabled"):
        directive_prompt = _build_directive_prompt(directive)
        if directive_prompt:
            dynamic_parts.append(directive_prompt)

    dynamic_text = "\n\n".join(dynamic_parts) if dynamic_parts else "（暂无动态内容）"
    sections.append({"title": "动态层（NPC指令 + 关系 + 时间 + 剧情 + 导演指令）", "content": dynamic_text})

    # 4. 对话层
    short_memory_text = "\n".join(
        f"[{msg.get('role', '?')}]: {msg.get('content', '')[:200]}"
        for msg in rpb.memory.memory[-10:]
    ) if rpb.memory.memory else "（暂无对话历史）"
    sections.append({"title": "对话层（最近 10 条）", "content": short_memory_text})

    return render_template("prompt_preview.html", ctx=ctx, sections=sections,
                           world_name=world.WORLD_NAME)
