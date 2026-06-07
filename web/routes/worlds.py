"""世界编辑器路由。"""

import ast
import logging
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log
from web.routes.auth import login_required
from web.app import _flash_redirect

logger = logging.getLogger(__name__)

worlds_bp = Blueprint("worlds", __name__, url_prefix="/worlds")


@worlds_bp.route("/")
@login_required
def list_worlds():
    ctx = _ctx()
    worlds_dir = settings.BASE_DIR / "worlds"
    active = ctx.world.WORLD_NAME
    world_list = []
    for py_file in sorted(worlds_dir.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        size_kb = py_file.stat().st_size / 1024
        world_list.append({
            "name": name,
            "path": str(py_file.relative_to(settings.BASE_DIR)),
            "size_kb": size_kb,
            "is_active": name == active,
        })
    return render_template("worlds.html", worlds=world_list, active=active, ctx=ctx)


@worlds_bp.route("/<name>", methods=["GET"])
@login_required
def edit_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
    if not file_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"), f"世界 '{name}' 不存在", "error")

    if request.args.get("mode") == "raw":
        content = file_path.read_text(encoding="utf-8")
        return render_template("world_source_edit.html",
                               name=name, content=content,
                               file_path=str(file_path.relative_to(settings.BASE_DIR)),
                               ctx=_ctx())

    # 表单模式：解析 .py 提取字段
    try:
        fields = _parse_world_py(file_path)
    except Exception as exc:
        logger.warning("Failed to parse world %s: %s", name, exc)
        content = file_path.read_text(encoding="utf-8")
        return render_template("world_source_edit.html",
                               name=name, content=content,
                               file_path=str(file_path.relative_to(settings.BASE_DIR)),
                               error=f"无法解析世界文件，请使用源码模式编辑: {exc}",
                               ctx=_ctx())

    return render_template("world_edit.html",
                           name=name, fields=fields,
                           file_path=str(file_path.relative_to(settings.BASE_DIR)),
                           ctx=_ctx())


@worlds_bp.route("/<name>/save", methods=["POST"])
@login_required
def save_world(name: str):
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
    if not file_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"), f"世界 '{name}' 不存在", "error")

    mode = request.form.get("mode", "form")

    if mode == "raw":
        content = request.form.get("content", "")
        # 语法检查
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return render_template("world_source_edit.html",
                                   name=name, content=content,
                                   file_path=str(file_path.relative_to(settings.BASE_DIR)),
                                   error=f"语法错误: {exc}",
                                   ctx=_ctx())

        # 备份 → 写入
        content_bytes = content.encode("utf-8")
        old = file_path.read_bytes()
        file_path.write_text(content, encoding="utf-8")

        # 尝试加载验证
        try:
            import importlib
            importlib.invalidate_caches()
            importlib.import_module(f"worlds.{name}")
        except Exception as exc:
            # 回滚
            file_path.write_bytes(old)
            return render_template("world_source_edit.html",
                                   name=name, content=content,
                                   file_path=str(file_path.relative_to(settings.BASE_DIR)),
                                   error=f"世界文件加载失败，已回滚: {exc}",
                                   ctx=_ctx())

        audit_log("编辑世界", f"源码模式保存 {name}.py")
        logger.info("Web panel: saved world file %s (raw)", name)
        return _flash_redirect(url_for("worlds.edit_world", name=name),
                               f"{name}.py 已保存")

    # 表单模式
    new_values: dict[str, str] = {}
    for field in ["WORLD_NAME", "START_SCENE", "SYSTEM_PROMPT",
                   "CHARACTERS", "RULES", "LOCATIONS", "EVENT_POOL", "NPCS"]:
        val = (request.form.get(f"field_{field}") or "").strip()
        new_values[field] = val

    try:
        fields = _parse_world_py(file_path)
        _write_world_py(file_path, fields, new_values)
        audit_log("编辑世界", f"表单模式保存 {name}.py")
        logger.info("Web panel: saved world file %s (form)", name)
        return _flash_redirect(url_for("worlds.edit_world", name=name),
                               f"{name}.py 已保存")
    except Exception as exc:
        logger.error("Failed to save world %s: %s", name, exc)
        fields_fallback = {k: v for k, v in new_values.items()}
        return render_template("world_edit.html",
                               name=name, fields=fields_fallback,
                               file_path=str(file_path.relative_to(settings.BASE_DIR)),
                               error=f"保存失败: {exc}",
                               ctx=_ctx())


@worlds_bp.route("/switch", methods=["POST"])
@login_required
def switch_world():
    new_world = (request.form.get("world") or "").strip()
    if not new_world.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    file_path = settings.BASE_DIR / "worlds" / f"{new_world}.py"
    if not file_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界文件 worlds/{new_world}.py 不存在", "error")

    _update_env("ACTIVE_WORLD", new_world)
    audit_log("切换世界", f"{new_world}")
    logger.info("Web panel: switched ACTIVE_WORLD to %s", new_world)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"已切换至 {new_world}，请重启 Bot 生效")


@worlds_bp.route("/create", methods=["POST"])
@login_required
def create_world():
    """新建世界：基于 one.py 模板。"""
    new_name = (request.form.get("world_name") or "").strip().lower()
    if not new_name or not new_name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    dest = settings.BASE_DIR / "worlds" / f"{new_name}.py"
    if dest.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{new_name}' 已存在", "error")

    template = settings.BASE_DIR / "worlds" / "one.py"
    content = template.read_text(encoding="utf-8")
    # 替换 WORLD_NAME
    content = content.replace('WORLD_NAME = "one"', f'WORLD_NAME = "{new_name}"')
    dest.write_text(content, encoding="utf-8")
    audit_log("创建世界", new_name)
    logger.info("Web panel: created world %s from one.py", new_name)
    return _flash_redirect(url_for("worlds.edit_world", name=new_name),
                           f"世界 '{new_name}' 已创建，请编辑内容")


@worlds_bp.route("/<name>/copy", methods=["POST"])
@login_required
def copy_world(name: str):
    """复制世界。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    src = settings.BASE_DIR / "worlds" / f"{name}.py"
    if not src.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    # 生成新名称
    copy_name = f"{name}_copy"
    dest = settings.BASE_DIR / "worlds" / f"{copy_name}.py"
    counter = 1
    while dest.exists():
        copy_name = f"{name}_copy{counter}"
        dest = settings.BASE_DIR / "worlds" / f"{copy_name}.py"
        counter += 1

    content = src.read_text(encoding="utf-8")
    # 替换 WORLD_NAME
    import re
    content = re.sub(r'WORLD_NAME\s*=\s*"[^"]*"', f'WORLD_NAME = "{copy_name}"', content)
    dest.write_text(content, encoding="utf-8")
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

    file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
    if not file_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"),
                               f"世界 '{name}' 不存在", "error")

    ctx = _ctx()
    if name == ctx.world.WORLD_NAME:
        return _flash_redirect(url_for("worlds.list_worlds"),
                               "不能删除当前激活的世界", "error")

    # 备份后删除
    backup = file_path.with_suffix(".py.deleted")
    file_path.rename(backup)
    audit_log("删除世界", name)
    logger.info("Web panel: deleted world %s (backed up to .py.deleted)", name)
    return _flash_redirect(url_for("worlds.list_worlds"),
                           f"世界 '{name}' 已删除（备份为 .py.deleted）")


@worlds_bp.route("/<name>/preview")
@login_required
def preview_prompt(name: str):
    """预览最终构建后的系统 Prompt。"""
    if not name.isidentifier():
        return _flash_redirect(url_for("worlds.list_worlds"), "无效的世界名", "error")

    file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
    if not file_path.exists():
        return _flash_redirect(url_for("worlds.list_worlds"), f"世界 '{name}' 不存在", "error")

    ctx = _ctx()
    try:
        import importlib
        import sys
        world_module = importlib.import_module(f"worlds.{name}")
        # 如果模块已加载过缓存，重新加载以获取最新修改
        if f"worlds.{name}" in sys.modules:
            world_module = importlib.reload(sys.modules[f"worlds.{name}"])
    except Exception as exc:
        return _flash_redirect(url_for("worlds.edit_world", name=name),
                               f"无法加载世界文件: {exc}", "error")

    # 构建系统 Prompt（与 generate_reply 逻辑一致，但不含动态数据）
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
# 世界文件解析与写回（从旧 web/templates.py 迁移）
# ═══════════════════════════════════════════════════════════

def _parse_world_py(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fields: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        # 处理普通赋值: X = value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if name.startswith("_"):
                    continue
                try:
                    fields[name] = _ast_value_to_form(node.value)
                except Exception:
                    fields[name] = ast.get_source_segment(source, node.value) or ""
        # 处理带类型标注的赋值: X: type = value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name.startswith("_"):
                continue
            if node.value is None:
                # 只有类型标注没有赋值（如 x: int），跳过
                continue
            try:
                fields[name] = _ast_value_to_form(node.value)
            except Exception:
                fields[name] = ast.get_source_segment(source, node.value) or ""
    return fields


def _ast_value_to_form(node) -> str:
    if isinstance(node, ast.Constant):
        val = node.value
        return val if isinstance(val, str) else str(val)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            else:
                parts.append("{...}")
        return "".join(parts)
    if isinstance(node, ast.List):
        items = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
            else:
                try:
                    items.append(ast.unparse(elt))
                except Exception:
                    items.append(str(elt))
        return "\n".join(items)
    if isinstance(node, ast.Dict):
        lines = []
        for k, v in zip(node.keys, node.values):
            key_str = str(k.value) if isinstance(k, ast.Constant) else f"({ast.dump(k)})"
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                val_str = v.value
            else:
                try:
                    val_str = ast.unparse(v)
                except Exception:
                    val_str = ""
            if val_str:
                lines.append(f"{key_str}: {val_str}")
        return "\n".join(lines)
    try:
        return ast.unparse(node)
    except Exception:
        return str(ast.dump(node))


def _write_world_py(path: Path, fields: dict[str, str], new_values: dict[str, str]) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    field_ranges: dict[str, tuple[int, int]] = {}
    for node in ast.iter_child_nodes(tree):
        # 普通赋值: X = value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    end = getattr(node, "end_lineno", node.lineno)
                    field_ranges[target.id] = (node.lineno, end)
                    break
        # 带类型标注的赋值: X: type = value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                end = getattr(node, "end_lineno", node.lineno)
                field_ranges[target.id] = (node.lineno, end)

    lines = source.splitlines()
    for name, (start, end) in sorted(field_ranges.items(), key=lambda x: -x[1][0]):
        if name not in new_values:
            continue
        new_val = new_values[name]
        if new_val == "":
            continue
        new_lines = _format_field_assignment(name, new_val)
        lines[start - 1 : end] = new_lines

    new_source = "\n".join(lines) + "\n"
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        raise ValueError(f"生成的代码有语法错误: {exc}") from exc

    # 备份
    backup = path.with_suffix(".py.bak")
    try:
        backup.write_text(source, encoding="utf-8")
    except Exception:
        pass
    path.write_text(new_source, encoding="utf-8")


def _format_field_assignment(name: str, value: str) -> list[str]:
    if "\n" not in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return [f'{name} = "{escaped}"']
    if name in ("START_SCENE", "SYSTEM_PROMPT"):
        lines = [f"{name} = ("]
        for line in value.split("\n"):
            escaped = line.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    "{escaped}\\n"')
        lines.append(")")
        return lines
    if name in ("CHARACTERS", "LOCATIONS"):
        lines = [f"{name}: dict[str, str] = {{"]
        for line in value.split("\n"):
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                ek = k.replace("\\", "\\\\").replace('"', '\\"')
                ev = v.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    "{ek}": "{ev}",')
        lines.append("}")
        return lines
    if name in ("RULES", "EVENT_POOL"):
        lines = [f"{name}: list[str] = ["]
        for line in value.split("\n"):
            line = line.strip()
            if line:
                escaped = line.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    "{escaped}",')
        lines.append("]")
        return lines
    if name == "NPCS":
        try:
            import json as _json
            data = _json.loads(value)
            formatted = _json.dumps(data, ensure_ascii=False, indent=4)
            return [f"{name}: dict[str, dict] = {formatted}"]
        except Exception:
            pass
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return [f'{name} = "{escaped}"']
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return [f'{name} = """{escaped}"""']


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
