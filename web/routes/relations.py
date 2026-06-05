"""关系网络路由。"""

import json
import logging

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
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

    if request.args.get("mode") == "raw":
        data = {
            "characters": rm.characters,
            "relations": rm.relations,
            "_reply_count_since_extract": rm._reply_count_since_extract,
        }
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        return render_template("relations.html",
                               relations=rm.relations,
                               dim_labels=DIM_LABELS,
                               raw_mode=True,
                               json_text=json_text,
                               error=None,
                               ctx=ctx)

    return render_template("relations.html",
                           relations=rm.relations,
                           dim_labels=DIM_LABELS,
                           raw_mode=False,
                           error=None,
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
            ctx.relationship_manager.characters = data.get("characters", [])
            ctx.relationship_manager.relations = data.get("relations", {})
            ctx.relationship_manager._reply_count_since_extract = data.get(
                "_reply_count_since_extract", 0,
            )
            ctx.relationship_manager.save()
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
                                   ctx=ctx)

    # 结构化模式
    dims = ["affection", "trust", "fear", "dependence", "suspicion", "hostility"]
    new_relations: dict[str, dict] = {}

    # 处理已有关系
    i = 0
    while f"rel_{i}_from" in request.form:
        if request.form.get(f"rel_{i}_delete") == "1":
            i += 1
            continue
        frm = (request.form.get(f"rel_{i}_from") or "").strip()
        to = (request.form.get(f"rel_{i}_to") or "").strip()
        if frm and to and frm != to:
            key = f"{frm}->{to}"
            rel = {}
            for dim in dims:
                try:
                    rel[dim] = max(-100, min(110, int(request.form.get(f"rel_{i}_{dim}", "0") or 0)))
                except ValueError:
                    rel[dim] = 0
            notes_text = (request.form.get(f"rel_{i}_notes") or "").strip()
            rel["notes"] = [n.strip() for n in notes_text.split("\n") if n.strip()]
            rel["last_updated"] = 0
            new_relations[key] = rel
        i += 1

    # 新增关系
    new_from = (request.form.get("new_from") or "").strip()
    new_to = (request.form.get("new_to") or "").strip()
    if new_from and new_to and new_from != new_to:
        key = f"{new_from}->{new_to}"
        rel = {}
        for dim in dims:
            try:
                rel[dim] = max(-100, min(110, int(request.form.get(f"new_{dim}", "0") or 0)))
            except ValueError:
                rel[dim] = 0
        notes_text = (request.form.get("new_notes") or "").strip()
        rel["notes"] = [n.strip() for n in notes_text.split("\n") if n.strip()]
        rel["last_updated"] = 0
        new_relations[key] = rel

    # 更新角色列表
    chars: set[str] = set()
    for key in new_relations:
        parts = key.split("->", 1)
        if len(parts) == 2:
            chars.add(parts[0].strip())
            chars.add(parts[1].strip())

    ctx.relationship_manager.characters = sorted(chars)
    ctx.relationship_manager.relations = new_relations
    ctx.relationship_manager.save()
    audit_log("编辑关系", "结构化模式")
    logger.info("Web panel: saved relationships (structured) for %s", ctx.world.WORLD_NAME)
    return _flash_redirect(url_for("relations.index"), "关系网络已保存")
