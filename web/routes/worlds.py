"""世界编辑器路由 —— 读写 data/worlds/*.yaml，不再暴露 Python 源码。"""

import json
import logging
import yaml
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

# 使用 YAML | 块文本的字段（长文本）
BLOCK_SCALAR_FIELDS = {"START_SCENE", "SYSTEM_PROMPT"}


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

@worlds_bp.route("/")
@login_required
def list_worlds():
    ctx = _ctx()
    active = ctx.world.WORLD_NAME
    data_dir = settings.BASE_DIR / "data" / "worlds"

    world_list = []
    seen: set[str] = set()

    # 优先：YAML 数据世界
    for yaml_file in sorted(data_dir.glob("*.yaml")):
        name = yaml_file.stem
        if name.startswith("_"):
            continue
        seen.add(name)
        size_kb = yaml_file.stat().st_size / 1024
        world_list.append({
            "name": name,
            "path": str(yaml_file.relative_to(settings.BASE_DIR)),
            "size_kb": size_kb,
            "is_active": name == active,
            "source": "yaml",
        })

    # 补充：旧 JSON 世界（尚未迁移到 YAML）
    for json_file in sorted(data_dir.glob("*.json")):
        name = json_file.stem
        if name.startswith("_") or name in seen:
            continue
        size_kb = json_file.stat().st_size / 1024
        world_list.append({
            "name": name,
            "path": str(json_file.relative_to(settings.BASE_DIR)),
            "size_kb": size_kb,
            "is_active": name == active,
            "source": "json",
        })

    return render_template("worlds.html", worlds=world_list, active=active, ctx=ctx)


@worlds_bp.route("/<name>", methods=["GET"])
@login_required
def edit_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.yaml"
    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"

    # ── YAML 优先 ──
    if yaml_path.exists():
        try:
            fields = _load_world_yaml(yaml_path)
        except Exception as exc:
            logger.warning("Failed to load world YAML %s: %s", name, exc)
            return _flash_redirect(url_for("worlds.list_worlds"),
                                   f"YAML 解析失败: {exc}", "error")
        return render_template("world_edit.html",
                               name=name, fields=fields,
                               file_path=str(yaml_path.relative_to(settings.BASE_DIR)),
                               source="yaml",
                               ctx=_ctx())

    # ── JSON 兜底（只读）──
    if json_path.exists():
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

    return _flash_redirect(url_for("worlds.list_worlds"), f"世界 '{name}' 不存在", "error")


@worlds_bp.route("/<name>/save", methods=["POST"])
@login_required
def save_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.yaml"
    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"

    if not yaml_path.exists() and not json_path.exists():
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
                                   file_path=str(yaml_path.relative_to(settings.BASE_DIR)),
                                   source="yaml",
                                   error="\n".join(errors),
                                   ctx=_ctx())
        _save_world_yaml(yaml_path, world_data)
    except Exception as exc:
        logger.error("Failed to save world %s: %s", name, exc)
        fields_fallback = {k: v for k, v in new_values.items()}
        return render_template("world_edit.html",
                               name=name, fields=fields_fallback,
                               file_path=str(yaml_path.relative_to(settings.BASE_DIR)),
                               source="yaml",
                               error=f"保存失败: {exc}",
                               ctx=_ctx())

    audit_log("编辑世界", f"表单模式保存 {name}.yaml")
    logger.info("Web panel: saved world YAML %s", name)

    # 如果保存的是当前激活的世界，触发热重载
    ctx = _ctx()
    if name == ctx.world_manager.world_name:
        ctx.world_manager.reload_world()
    return _flash_redirect(url_for("worlds.edit_world", name=name),
                           f"{name}.yaml 已保存")


@worlds_bp.route("/switch", methods=["POST"])
@login_required
def switch_world():
    new_world = (request.form.get("world") or "").strip()
    if not new_world.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{new_world}.yaml"
    json_path = settings.BASE_DIR / "data" / "worlds" / f"{new_world}.json"
    if not yaml_path.exists() and not json_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{new_world}' 不存在", "error")

    ctx = _ctx()
    ok = ctx.world_manager.switch_world(new_world)
    if not ok:
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"切换世界 '{new_world}' 失败", "error")

    audit_log("切换世界", f"{new_world} (即时生效)")
    logger.info("Web panel: switched active world to %s (hot-reload)", new_world)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"已切换至 {new_world}，下次聊天立即生效")


@worlds_bp.route("/create", methods=["POST"])
@login_required
def create_world():
    """新建世界：基于 one.yaml 模板创建 YAML 数据文件。"""
    new_name = (request.form.get("world_name") or "").strip().lower()
    if not new_name or not new_name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    yaml_dest = settings.BASE_DIR / "data" / "worlds" / f"{new_name}.yaml"
    if yaml_dest.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{new_name}' 已存在", "error")

    # 从 one.yaml 模板复制
    template_yaml = settings.BASE_DIR / "data" / "worlds" / "one.yaml"
    if template_yaml.exists():
        data = yaml.safe_load(template_yaml.read_text(encoding="utf-8"))
    else:
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
    yaml_dest.parent.mkdir(parents=True, exist_ok=True)
    _save_world_yaml(yaml_dest, data)

    audit_log("创建世界", new_name)
    logger.info("Web panel: created world YAML %s", new_name)
    return _flash_redirect(url_for("worlds.edit_world", name=new_name),
                           f"世界 '{new_name}' 已创建，请编辑内容")


@worlds_bp.route("/<name>/copy", methods=["POST"])
@login_required
def copy_world(name: str):
    """复制世界。优先复制 YAML，否则 JSON。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    yaml_src = settings.BASE_DIR / "data" / "worlds" / f"{name}.yaml"
    json_src = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"

    # 生成新名称
    copy_name = f"{name}_copy"
    counter = 1
    while (settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.yaml").exists() or \
          (settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.json").exists():
        copy_name = f"{name}_copy{counter}"
        counter += 1

    if yaml_src.exists():
        data = yaml.safe_load(yaml_src.read_text(encoding="utf-8"))
        data["WORLD_NAME"] = copy_name
        yaml_dest = settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.yaml"
        yaml_dest.parent.mkdir(parents=True, exist_ok=True)
        _save_world_yaml(yaml_dest, data)
    elif json_src.exists():
        data = json.loads(json_src.read_text(encoding="utf-8"))
        data["WORLD_NAME"] = copy_name
        yaml_dest = settings.BASE_DIR / "data" / "worlds" / f"{copy_name}.yaml"
        yaml_dest.parent.mkdir(parents=True, exist_ok=True)
        _save_world_yaml(yaml_dest, data)
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

    yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.yaml"
    json_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.json"

    if not yaml_path.exists() and not json_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    ctx = _ctx()
    if name == ctx.world.WORLD_NAME:
        return _flash_redirect(url_for("worlds.list_worlds"),
                               "不能删除当前激活的世界", "error")

    # 备份后删除
    for path in [yaml_path, json_path]:
        if path.exists():
            backup = path.with_suffix(path.suffix + ".deleted")
            path.rename(backup)

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
# YAML 世界读写
# ═══════════════════════════════════════════════════════════

def _load_world_yaml(path: Path) -> dict[str, str]:
    """从 YAML 加载世界数据，结构化字段转换为表单行格式。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("YAML 根节点必须是字典")
    fields: dict[str, str] = {}
    for key in FORM_FIELDS:
        value = data.get(key, "")
        fields[key] = _struct_to_form(value, key)
    return fields


def _load_world_json(path: Path) -> dict[str, str]:
    """从旧 JSON 加载世界数据（过渡期兼容）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    fields: dict[str, str] = {}
    for key in FORM_FIELDS:
        value = data.get(key, "")
        fields[key] = _struct_to_form(value, key)
    return fields


def _save_world_yaml(path: Path, data: dict) -> None:
    """原子写入 YAML + 自动备份。

    1. 先生成 YAML 文本（长文本使用 | 块文本）
    2. 写入临时文件
    3. 校验能重新解析
    4. 备份旧文件
    5. 替换正式文件
    """
    yaml_text = _dump_world_yaml(data)

    # 写入临时文件
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml_text, encoding="utf-8")

    # 校验
    try:
        parsed = yaml.safe_load(tmp.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("YAML 根节点必须是字典")
    except Exception as exc:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise ValueError(f"YAML 校验失败: {exc}") from exc

    # 备份旧文件
    backup_dir = settings.BASE_DIR / "backups" / "worlds"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            import shutil
            backup_name = f"{path.stem}_{_backup_timestamp()}.yaml"
            shutil.copy2(path, backup_dir / backup_name)
        except Exception as exc:
            logger.warning("Failed to backup %s: %s", path, exc)

    # 原子替换
    tmp.replace(path)


def _backup_timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _dump_world_yaml(data: dict) -> str:
    """将结构化世界数据转为 YAML 文本。

    START_SCENE 和 SYSTEM_PROMPT 使用 | 块文本风格，
    保证多行文本可手动编辑。
    """
    lines = []
    for key in FORM_FIELDS:
        value = data.get(key, "")
        if not lines:
            pass  # 第一个字段不需要前置空行
        else:
            lines.append("")

        if key == "WORLD_NAME":
            lines.append(f"{key}: {_yaml_str(value)}")

        elif key in BLOCK_SCALAR_FIELDS:
            text = str(value) if value else ""
            if "\n" in text:
                lines.append(f"{key}: |")
                for line in text.split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"{key}: {_yaml_str(text)}")

        elif key in DICT_LINE_FIELDS:
            d = value if isinstance(value, dict) else {}
            lines.append(f"{key}:")
            if d:
                for k, v in d.items():
                    lines.append(f"  {_yaml_str(k)}: {_yaml_str(str(v))}")
            else:
                lines.append("  {}")

        elif key in LIST_LINE_FIELDS:
            lst = value if isinstance(value, list) else []
            lines.append(f"{key}:")
            if lst:
                for item in lst:
                    lines.append(f"  - {_yaml_str(str(item))}")
            else:
                lines.append("  []")

        elif key in JSON_FIELDS:
            d = value if isinstance(value, dict) else {}
            if d:
                lines.append(f"{key}:")
                # 用 PyYAML 生成嵌套 dict（流畅样式）
                nested = yaml.dump(d, allow_unicode=True, default_flow_style=False,
                                   sort_keys=False, indent=2)
                for line in nested.strip().split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"{key}: {{}}")

    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """返回安全的 YAML 标量字符串（必要时加引号）。"""
    if not s:
        return "''"
    # 需引号的字符
    special = set(":#{}[]&*?!|-><=!%@`'\"")
    if s.startswith(" ") or s.endswith(" ") or any(c in s for c in special):
        return json.dumps(s, ensure_ascii=False)
    if s.lower() in ("true", "false", "null", "yes", "no", "on", "off") or s.isdigit():
        return json.dumps(s, ensure_ascii=False)
    return s


# ═══════════════════════════════════════════════════════════
# 结构化 ↔ 表单格式转换
# ═══════════════════════════════════════════════════════════

def _struct_to_form(value, field_name: str) -> str:
    """结构化值 → 表单字符串。"""
    if field_name in DICT_LINE_FIELDS:
        if not isinstance(value, dict):
            return str(value) if value else ""
        return "\n".join(f"{k}: {v}" for k, v in value.items())

    if field_name in LIST_LINE_FIELDS:
        if not isinstance(value, list):
            return str(value) if value else ""
        return "\n".join(str(item) for item in value)

    if field_name in JSON_FIELDS:
        if not isinstance(value, dict):
            return str(value) if value else "{}"
        return json.dumps(value, ensure_ascii=False, indent=2)

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
        raise ValueError(f"NPCS 字段不是有效的 JSON 对象")

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

    wn = data.get("WORLD_NAME", "")
    if not wn or not isinstance(wn, str) or not wn.strip():
        errors.append("世界名不能为空")
    elif not wn.strip().isidentifier():
        errors.append(f"世界名 '{wn}' 不是有效标识符")

    for field, label in [("START_SCENE", "开场场景"), ("SYSTEM_PROMPT", "系统提示词")]:
        if not isinstance(data.get(field, ""), str):
            errors.append(f"{label}必须是文本")

    for field, label in [("CHARACTERS", "角色设定"), ("LOCATIONS", "地点设定")]:
        if not isinstance(data.get(field, {}), dict):
            errors.append(f"{label}必须是键值对格式")

    for field, label in [("RULES", "剧情规则"), ("EVENT_POOL", "事件池")]:
        if not isinstance(data.get(field, []), list):
            errors.append(f"{label}必须是列表格式")

    if not isinstance(data.get("NPCS", {}), dict):
        errors.append("NPC 配置必须是 JSON 对象格式")

    return errors


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
