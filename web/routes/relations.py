"""关系网络路由。"""

import json
import logging

from flask import Blueprint, render_template, request, url_for

from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

relations_bp = Blueprint("relations", __name__, url_prefix="/relations")

DIM_LABELS = [
    ("affection", "好感"),
    ("trust", "信任"),
    ("fear", "畏惧"),
    ("dependence", "依赖"),
    ("suspicion", "怀疑"),
    ("hostility", "敌意"),
]


@relations_bp.route("/")
@login_required
def index():
    ctx = _ctx()
    rm = ctx.relationship_manager
    debug_info = rm.get_debug_info()

    if request.args.get("mode") == "raw":
        data = {
            "characters": rm.characters,
            "relations": rm.relations,
            "_reply_count_since_extract": rm._reply_count_since_extract,
            "revision": rm.revision,
            "last_modified_source": rm.last_modified_source,
            "last_modified_at": rm.last_modified_at,
            "last_change": rm.last_change,
        }
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        return render_template("relations.html",
                               relations=rm.relations,
                               dim_labels=DIM_LABELS,
                               raw_mode=True,
                               json_text=json_text,
                               error=None,
                               debug_info=debug_info,
                               ctx=ctx)

    return render_template("relations.html",
                           relations=rm.relations,
                           dim_labels=DIM_LABELS,
                           raw_mode=False,
                           error=None,
                           debug_info=debug_info,
                           ctx=ctx)


@relations_bp.route("/save", methods=["POST"])
@login_required
def save():
    ctx = _ctx()
    mode = request.form.get("mode", "structured")

    if mode == "raw":
        content = request.form.get("content", "")
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("JSON 必须是对象")
            if not isinstance(data.get("characters", []), list):
                raise ValueError("characters 必须是数组")
            if not isinstance(data.get("relations", {}), dict):
                raise ValueError("relations 必须是对象")
            rm = ctx.relationship_manager
            with rm._lock:
                before_state = rm.snapshot_state()
                rm.characters = data.get("characters", [])
                rm.relations = data.get("relations", {})
                rm._reply_count_since_extract = data.get("_reply_count_since_extract", 0)
                rm.commit_web_manual_change(before_state, action="raw_save")
            audit_log("编辑关系", "JSON 模式")
            return _flash_redirect(url_for("relations.index"), "关系网络已保存")
        except (json.JSONDecodeError, ValueError) as exc:
            json_text = content
            return render_template("relations.html",
                                   relations=ctx.relationship_manager.relations,
                                   dim_labels=DIM_LABELS,
                                   raw_mode=True,
                                   json_text=json_text,
                                   error=f"JSON 格式错误: {exc}",
                                   debug_info=ctx.relationship_manager.get_debug_info(),
                                   ctx=ctx)

    # 结构化模式 — 增量更新，不清除 AI 自动抽取的关系
    dims = ["affection", "trust", "fear", "dependence", "suspicion", "hostility"]
    rm = ctx.relationship_manager

    with rm._lock:
        before_state = rm.snapshot_state()
        # 从现有关系出发，不清除不在表单中的关系（如 AI 抽取的新角色对）
        form_keys: set[str] = set()

        # 处理已有关系：更新或删除
        i = 0
        while f"rel_{i}_from" in request.form:
            frm = (request.form.get(f"rel_{i}_from") or "").strip()
            to = (request.form.get(f"rel_{i}_to") or "").strip()
            if frm and to and frm != to:
                key = f"{frm}->{to}"
                form_keys.add(key)

                if request.form.get(f"rel_{i}_delete") == "1":
                    # 用户勾选删除
                    if key in rm.relations:
                        del rm.relations[key]
                        logger.info("Web panel: deleted relation %s", key)
                else:
                    # 增量更新：保留已有 notes/last_updated，只更新维度值
                    existing = rm.relations.get(key, rm._empty_relation())
                    for dim in dims:
                        try:
                            existing[dim] = max(-100, min(110, int(request.form.get(f"rel_{i}_{dim}", "0") or 0)))
                        except ValueError:
                            pass  # 保留原值
                    notes_text = (request.form.get(f"rel_{i}_notes") or "").strip()
                    if notes_text:
                        new_notes = [n.strip() for n in notes_text.split("\n") if n.strip()]
                        if new_notes != existing.get("notes", []):
                            existing["notes"] = new_notes
                    # 不覆盖 last_updated（保留最近一次变更的真实记录）
                    rm.relations[key] = existing
            i += 1

        # 新增关系
        new_from = (request.form.get("new_from") or "").strip()
        new_to = (request.form.get("new_to") or "").strip()
        if new_from and new_to and new_from != new_to:
            key = f"{new_from}->{new_to}"
            if key not in rm.relations:
                rel = rm._empty_relation()
                for dim in dims:
                    try:
                        rel[dim] = max(-100, min(110, int(request.form.get(f"new_{dim}", "0") or 0)))
                    except ValueError:
                        pass
                notes_text = (request.form.get("new_notes") or "").strip()
                if notes_text:
                    rel["notes"] = [n.strip() for n in notes_text.split("\n") if n.strip()]
                rm.relations[key] = rel
                form_keys.add(key)

        # 更新角色列表（包含所有 known chars，不丢失 AI 发现的角色）
        chars: set[str] = set(rm.characters)
        for key in rm.relations:
            parts = key.split("->", 1)
            if len(parts) == 2:
                chars.add(parts[0].strip())
                chars.add(parts[1].strip())
        rm.characters = sorted(chars)

        rm.commit_web_manual_change(before_state, action="structured_save")

    audit_log("编辑关系", f"结构化模式（{len(form_keys)} 个角色对）")
    logger.info("Web panel: saved relationships (structured) for %s: %d pairs",
                ctx.world.WORLD_NAME, len(form_keys))
    return _flash_redirect(url_for("relations.index"), f"关系网络已保存（{len(form_keys)} 个角色对）")
