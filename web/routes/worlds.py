"""世界编辑器路由 —— 读写 data/worlds/*.json，不再暴露 Python 源码。"""

import ast
import json
import logging
import tempfile
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

worlds_bp = Blueprint("worlds", __name__, url_prefix="/worlds")

# ── 字段元数据 ──────────────────────────────────────────

FORM_FIELDS = [
    "WORLD_NAME", "START_SCENE", "SYSTEM_PROMPT",
    "CHARACTERS", "RULES", "LOCATIONS", "EVENT_POOL", "NPCS",
]

# 结构化存储为 dict，在表单中以 "key: value" 行格式显示
DICT_LINE_FIELDS = {"CHARACTERS", "LOCATIONS"}

# 结构化存储为 list，在表单中以行格式显示
LIST_LINE_FIELDS = {"RULES", "EVENT_POOL"}

# 结构化存储为嵌套 dict，在表单中以 JSON 字符串显示
JSON_FIELDS = {"NPCS"}


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

@worlds_bp.route("/")
@login_required
def list_worlds():
    ctx = _ctx()
    active = ctx.world.WORLD_NAME
    data_dir = settings.BASE_DIR / "data" / "worlds"
    py_dir = settings.BASE_DIR / "worlds"

    world_list = []
    seen: set[str] = set()

    # 优先：JSON 数据世界
    for json_file in sorted(data_dir.glob("*.json")):
        name = json_file.stem
        if name.startswith("_"):
            continue
        seen.add(name)
        size_kb = json_file.stat().st_size / 1024
        world_list.append({
            "name": name,
            "path": str(json_file.relative_to(settings.BASE_DIR)),
            "size_kb": size_kb,
            "is_active": name == active,
            "source": "json",
        })

    # 补充：尚未迁移的 .py 世界
    for py_file in sorted(py_dir.glob("*.py")):
        name = py_file.stem
        if name.startswith("_") or name in seen:
            continue
        size_kb = py_file.stat().st_size / 1024
        world_list.append({
            "name": name,
            "path": str(py_file.relative_to(settings.BASE_DIR)),
            "size_kb": size_kb,
            "is_active": name == active,
            "source": "py",
        })

    return render_template("worlds.html", worlds=world_list, active=active, ctx=ctx)


@worlds_bp.route("/<name>", methods=["GET"])
@login_required
def edit_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"
    py_path = settings.BASE_DIR / "worlds" / f"{name}.py"

    # ── JSON 世界：表单模式 ──
    if json_path.exists():
        if request.args.get("mode") == "raw":
            return _flash_redirect(url_for("worlds.edit_world", name=name),
                                   "JSON 世界不支持源码编辑模式", "info")
        try:
            fields = _load_world_json(json_path)
        except Exception as exc:
            logger.warning("Failed to load world JSON %s: %s", name, exc)
            return _flash_redirect(url_for("worlds.list_worlds"),
                                   f"无法读取世界数据: {exc}", "error")
        return render_template("world_edit.html",
                               name=name, fields=fields,
                               file_path=str(json_path.relative_to(settings.BASE_DIR)),
                               source="json",
                               ctx=_ctx())

    # ── 仅有 .py 文件：支持 raw 模式，表单模式则自动迁移 ──
    if py_path.exists():
        if request.args.get("mode") == "raw":
            content = py_path.read_text(encoding="utf-8")
            return render_template("world_source_edit.html",
                                   name=name, content=content,
                                   file_path=str(py_path.relative_to(settings.BASE_DIR)),
                                   ctx=_ctx())

        # 表单模式 → 自动迁移到 JSON
        try:
            fields = _migrate_py_to_json(name, py_path, json_path)
            audit_log("迁移世界", f"{name}.py → {name}.json")
            logger.info("Web panel: migrated %s.py to JSON", name)
        except Exception as exc:
            logger.warning("Failed to migrate world %s: %s", name, exc)
            content = py_path.read_text(encoding="utf-8")
            return render_template("world_source_edit.html",
                                   name=name, content=content,
                                   file_path=str(py_path.relative_to(settings.BASE_DIR)),
                                   error=f"迁移失败，请使用源码模式编辑: {exc}",
                                   ctx=_ctx())

        return render_template("world_edit.html",
                               name=name, fields=fields,
                               file_path=str(json_path.relative_to(settings.BASE_DIR)),
                               source="json",
                               ctx=_ctx())

    return _flash_redirect(url_for("worlds.list_worlds"), f"世界 '{name}' 不存在", "error")


@worlds_bp.route("/<name>/save", methods=["POST"])
@login_required
def save_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"
    py_path = settings.BASE_DIR / "worlds" / f"{name}.py"

    mode = request.form.get("mode", "form")

    # ── raw 模式：仅对未迁移 .py 开放 ──
    if mode == "raw":
        if json_path.exists():
            return _flash_redirect(url_for("worlds.edit_world", name=name),
                                   "JSON 世界不支持源码编辑模式", "error")
        if not py_path.exists():
            return _flash_redirect(url_for("worlds.list_worlds"),
                                   f"世界 '{name}' 不存在", "error")
        return _save_py_raw(name, py_path)

    # ── 表单模式：保存到 JSON ──
    if not json_path.exists() and not py_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    # 收集表单值
    new_values: dict[str, str] = {}
    for field in FORM_FIELDS:
        val = (request.form.get(f"field_{field}") or "")
        new_values[field] = val

    # 转换并校验
    try:
        world_data = _form_to_world_data(new_values)
        errors = _validate_world_fields(world_data)
        if errors:
            fields_fallback = {k: v for k, v in new_values.items()}
            return render_template("world_edit.html",
                                   name=name, fields=fields_fallback,
                                   file_path=str(json_path.relative_to(settings.BASE_DIR)),
                                   source="json",
                                   error="\n".join(errors),
                                   ctx=_ctx())
        _save_world_json(json_path, world_data)
    except Exception as exc:
        logger.error("Failed to save world %s: %s", name, exc)
        fields_fallback = {k: v for k, v in new_values.items()}
        return render_template("world_edit.html",
                               name=name, fields=fields_fallback,
                               file_path=str(json_path.relative_to(settings.BASE_DIR)),
                               source="json",
                               error=f"保存失败: {exc}",
                               ctx=_ctx())

    audit_log("编辑世界", f"表单模式保存 {name}.json")
    logger.info("Web panel: saved world JSON %s", name)
    return _flash_redirect(url_for("worlds.edit_world", name=name),
                           f"{name}.json 已保存")


@worlds_bp.route("/switch", methods=["POST"])
@login_required
def switch_world():
    new_world = (request.form.get("world") or "").strip()
    if not new_world.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_path = settings.BASE_DIR / "data" / "worlds" / f"{new_world}.json"
    py_path = settings.BASE_DIR / "worlds" / f"{new_world}.py"
    if not json_path.exists() and not py_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{new_world}' 不存在", "error")

    _update_env("ACTIVE_WORLD", new_world)
    audit_log("切换世界", f"{new_world}")
    logger.info("Web panel: switched ACTIVE_WORLD to %s", new_world)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"已切换至 {new_world}，请重启 Bot 生效")


@worlds_bp.route("/create", methods=["POST"])
@login_required
def create_world():
    """新建世界：基于 one.json 模板创建 JSON 数据文件。"""
    new_name = (request.form.get("world_name") or "").strip().lower()
    if not new_name or not new_name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_dest = settings.BASE_DIR / "data" / "worlds" / f"{new_name}.json"
    if json_dest.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{new_name}' 已存在", "error")

    # 从 one.json 模板复制
    template_json = settings.BASE_DIR / "data" / "worlds" / "one.json"
    if template_json.exists():
        data = json.loads(template_json.read_text(encoding="utf-8"))
    else:
        # 空模板
        data = {
            "WORLD_NAME": new_name,
            "START_SCENE": "",
            "SYSTEM_PROMPT": "",
            "CHARACTERS": {},
            "RULES": [],
            "LOCATIONS": {},
            "EVENT_POOL": [],
            "NPCS": {},
        }

    data["WORLD_NAME"] = new_name
    json_dest.parent.mkdir(parents=True, exist_ok=True)
    json_dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_log("创建世界", new_name)
    logger.info("Web panel: created world JSON %s", new_name)
    return _flash_redirect(url_for("worlds.edit_world", name=new_name),
                           f"世界 '{new_name}' 已创建，请编辑内容")


@worlds_bp.route("/<name>/copy", methods=["POST"])
@login_required
def copy_world(name: str):
    """复制世界。优先复制 JSON，否则复制 .py。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_src = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"
    py_src = settings.BASE_DIR / "worlds" / f"{name}.py"

    # 生成新名称
    copy_name = f"{name}_copy"
    counter = 1
    while (settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.json").exists() or \
          (settings.BASE_DIR / "worlds" / f"{copy_name}.py").exists():
        copy_name = f"{name}_copy{counter}"
        counter += 1

    if json_src.exists():
        data = json.loads(json_src.read_text(encoding="utf-8"))
        data["WORLD_NAME"] = copy_name
        json_dest = settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.json"
        json_dest.parent.mkdir(parents=True, exist_ok=True)
        json_dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif py_src.exists():
        import re
        content = py_src.read_text(encoding="utf-8")
        content = re.sub(r'WORLD_NAME\s*=\s*"[^"]*"', f'WORLD_NAME = "{copy_name}"', content)
        py_dest = settings.BASE_DIR / "worlds" / f"{copy_name}.py"
        py_dest.write_text(content, encoding="utf-8")
    else:
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    audit_log("复制世界", f"{name} -> {copy_name}")
    logger.info("Web panel: copied world %s to %s", name, copy_name)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"已复制为 '{copy_name}'")


@worlds_bp.route("/<name>/delete", methods=["POST"])
@login_required
def delete_world(name: str):
    """删除世界。需二次确认（前端已做）。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"
    py_path = settings.BASE_DIR / "worlds" / f"{name}.py"

    if not json_path.exists() and not py_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    ctx = _ctx()
    if name == ctx.world.WORLD_NAME:
        return _flash_redirect(url_for("worlds.list_worlds"),
                               "不能删除当前激活的世界", "error")

    # 备份后删除
    if json_path.exists():
        backup = json_path.with_suffix(".json.deleted")
        json_path.rename(backup)
    if py_path.exists():
        py_backup = py_path.with_suffix(".py.deleted")
        py_path.rename(py_backup)

    audit_log("删除世界", name)
    logger.info("Web panel: deleted world %s (backed up)", name)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"世界 '{name}' 已删除（已备份）")


@worlds_bp.route("/<name>/preview")
@login_required
def preview_prompt(name: str):
    """预览最终构建后的系统 Prompt。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    ctx = _ctx()
    try:
        from bot.utils import load_world
        world_module = load_world(name)
    except Exception as exc:
        return _flash_redirect(url_for("worlds.edit_world", name=name),
                               f"无法加载世界数据: {exc}", "error")

    # 如果是旧模块（.py），需要 reload
    import sys
    if hasattr(world_module, '__file__') and f"worlds.{name}" in sys.modules:
        import importlib
        world_module = importlib.reload(sys.modules[f"worlds.{name}"])

    system_prompt = world_module.SYSTEM_PROMPT

    # 关系摘要（如果当前世界匹配）
    if name == ctx.world.WORLD_NAME:
        from config import prompts
        relation_summary = ctx.relationship_manager.get_summary()
        if relation_summary:
            system_prompt += "\n" + prompts.RELATION_INJECT_INSTRUCTION + relation_summary
        time_summary = ctx.time_manager.get_summary()
        if time_summary:
            system_prompt += "\n" + time_summary

    return render_template("world_prompt_preview.html",
                           world_name=name,
                           prompt_text=system_prompt,
                           ctx=ctx)


# ═══════════════════════════════════════════════════════════
# JSON 世界读写
# ═══════════════════════════════════════════════════════════

def _load_world_json(path: Path) -> dict[str, str]:
    """从 JSON 加载世界数据，结构化字段转换为表单行格式。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    fields: dict[str, str] = {}
    for key in FORM_FIELDS:
        value = data.get(key, "")
        fields[key] = _struct_to_form(value, key)
    return fields


def _save_world_json(path: Path, data: dict) -> None:
    """原子写入 + 自动备份。

    1. 写入临时文件
    2. 校验 JSON 可解析
    3. 备份旧文件到 backups/worlds/
    4. 替换正式文件
    """
    json_text = json.dumps(data, ensure_ascii=False, indent=2)

    # 写入临时文件
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json_text, encoding="utf-8")

    # 校验
    try:
        json.loads(tmp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise ValueError(f"JSON 校验失败: {exc}") from exc

    # 备份旧文件
    backup_dir = settings.BASE_DIR / "backups" / "worlds"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            import shutil
            backup_name = f"{path.stem}_{_backup_timestamp()}.json"
            shutil.copy2(path, backup_dir / backup_name)
        except Exception as exc:
            logger.warning("Failed to backup %s: %s", path, exc)

    # 原子替换
    tmp.replace(path)


def _backup_timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ═══════════════════════════════════════════════════════════
# 结构化 ↔ 表单格式转换
# ═══════════════════════════════════════════════════════════

def _struct_to_form(value, field_name: str) -> str:
    """结构化值 → 表单字符串。"""
    if field_name in DICT_LINE_FIELDS:
        # dict → "key: value" 行
        if not isinstance(value, dict):
            return str(value) if value else ""
        return "\n".join(f"{k}: {v}" for k, v in value.items())

    if field_name in LIST_LINE_FIELDS:
        # list → 行文本
        if not isinstance(value, list):
            return str(value) if value else ""
        return "\n".join(str(item) for item in value)

    if field_name in JSON_FIELDS:
        # 嵌套 dict → 格式化 JSON
        if not isinstance(value, dict):
            return str(value) if value else "{}"
        return json.dumps(value, ensure_ascii=False, indent=2)

    # 纯字符串
    return str(value) if value else ""


def _form_to_struct(text: str, field_name: str):
    """表单字符串 → 结构化值。"""
    if field_name in DICT_LINE_FIELDS:
        result: dict[str, str] = {}
        for line in text.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    result[k] = v
        return result

    if field_name in LIST_LINE_FIELDS:
        return [line.strip() for line in text.strip().split("\n") if line.strip()]

    if field_name in JSON_FIELDS:
        text = text.strip()
        if not text or text == "{}":
            return {}
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        # JSON 解析失败时，尝试当作 Python dict literal
        try:
            result = ast.literal_eval(text)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        raise ValueError(f"NPCS 字段不是有效的 JSON 对象")

    # 纯字符串
    return text


def _form_to_world_data(new_values: dict[str, str]) -> dict:
    """表单提交的字符串字典 → 完整结构化世界数据。"""
    data: dict = {}
    for field in FORM_FIELDS:
        raw = (new_values.get(field) or "").strip()
        data[field] = _form_to_struct(raw, field)
    return data


# ═══════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════

def _validate_world_fields(data: dict) -> list[str]:
    """校验世界数据，返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []

    # WORLD_NAME
    wn = data.get("WORLD_NAME", "")
    if not wn or not isinstance(wn, str) or not wn.strip():
        errors.append("世界名不能为空")
    elif not wn.strip().isidentifier():
        errors.append(f"世界名 '{wn}' 不是有效标识符（只能包含字母、数字、下划线）")

    # START_SCENE
    ss = data.get("START_SCENE", "")
    if not isinstance(ss, str):
        errors.append("开场场景必须是文本")

    # SYSTEM_PROMPT
    sp = data.get("SYSTEM_PROMPT", "")
    if not isinstance(sp, str):
        errors.append("系统提示词必须是文本")

    # CHARACTERS
    chars = data.get("CHARACTERS", {})
    if not isinstance(chars, dict):
        errors.append("角色设定必须是键值对格式")

    # LOCATIONS
    locs = data.get("LOCATIONS", {})
    if not isinstance(locs, dict):
        errors.append("地点设定必须是键值对格式")

    # RULES
    rules = data.get("RULES", [])
    if not isinstance(rules, list):
        errors.append("剧情规则必须是列表格式")

    # EVENT_POOL
    ep = data.get("EVENT_POOL", [])
    if not isinstance(ep, list):
        errors.append("事件池必须是列表格式")

    # NPCS
    npcs = data.get("NPCS", {})
    if not isinstance(npcs, dict):
        errors.append("NPC 配置必须是 JSON 对象格式")

    return errors


# ═══════════════════════════════════════════════════════════
# 迁移：worlds/<name>.py → data/worlds/<name>.json
# ═══════════════════════════════════════════════════════════

def _migrate_py_to_json(name: str, py_path: Path, json_path: Path) -> dict[str, str]:
    """从旧 .py 文件迁移到 JSON，保留原 .py 不动。"""
    import importlib

    mod = importlib.import_module(f"worlds.{name}")

    # 获取实际值（不是 AST 解析，是真实 Python 值）
    world_data = {
        "WORLD_NAME": getattr(mod, "WORLD_NAME", name),
        "START_SCENE": getattr(mod, "START_SCENE", ""),
        "SYSTEM_PROMPT": getattr(mod, "SYSTEM_PROMPT", ""),
        "CHARACTERS": getattr(mod, "CHARACTERS", {}),
        "LOCATIONS": getattr(mod, "LOCATIONS", {}),
        "RULES": getattr(mod, "RULES", []),
        "EVENT_POOL": getattr(mod, "EVENT_POOL", []),
        "NPCS": getattr(mod, "NPCS", {}),
    }

    # 确保类型正确
    for key in DICT_LINE_FIELDS | JSON_FIELDS:
        if not isinstance(world_data[key], dict):
            world_data[key] = {}
    for key in LIST_LINE_FIELDS:
        if not isinstance(world_data[key], list):
            world_data[key] = []

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(world_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 读取转换后的表单字段
    return _load_world_json(json_path)


# ═══════════════════════════════════════════════════════════
# 旧 raw 模式保存（仅对未迁移 .py 开放）
# ═══════════════════════════════════════════════════════════

def _save_py_raw(name: str, py_path: Path):
    """源码模式保存 .py 文件。仅用于尚未迁移到 JSON 的旧世界。"""
    content = request.form.get("content", "")
    try:
        ast.parse(content)
    except SyntaxError as exc:
        return render_template("world_source_edit.html",
                               name=name, content=content,
                               file_path=str(py_path.relative_to(settings.BASE_DIR)),
                               error=f"语法错误: {exc}",
                               ctx=_ctx())

    old = py_path.read_bytes()
    py_path.write_text(content, encoding="utf-8")

    # 尝试加载验证
    import importlib
    try:
        importlib.invalidate_caches()
        importlib.import_module(f"worlds.{name}")
    except Exception as exc:
        py_path.write_bytes(old)
        return render_template("world_source_edit.html",
                               name=name, content=content,
                               file_path=str(py_path.relative_to(settings.BASE_DIR)),
                               error=f"世界文件加载失败，已回滚: {exc}",
                               ctx=_ctx())

    audit_log("编辑世界", f"源码模式保存 {name}.py")
    logger.info("Web panel: saved world file %s (raw)", name)
    return _flash_redirect(url_for("worlds.edit_world", name=name),
                           f"{name}.py 已保存")


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════

def _update_env(key: str, value: str) -> None:
    """更新 .env 文件中的键值对，保留原有格式。"""
    env_path = settings.BASE_DIR / ".env"
    if not env_path.exists():
        logger.warning(".env not found, cannot update %s", key)
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
