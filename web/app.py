"""Flask Web 管理面板。

在独立守护线程中运行，通过 HTTP Basic Auth 保护。
所有路由共享 Bot 的 world / memory / client / npc_manager 实例。
"""

import ast
import functools
import json
import logging
import os
import time
from pathlib import Path

from flask import Flask, Response, redirect, request

from config import settings
from web import templates


logger = logging.getLogger(__name__)


class AppContext:
    """Flask 与 Bot 共享的状态容器。"""
    def __init__(self, world, memory, client, npc_manager, relationship_manager, start_time: float):
        self.world = world
        self.memory = memory
        self.client = client
        self.npc_manager = npc_manager
        self.relationship_manager = relationship_manager
        self.start_time = start_time


def create_app(ctx: AppContext) -> Flask:
    app = Flask(__name__)
    app.config["ctx"] = ctx
    return app


def require_auth(func):
    """HTTP Basic Auth 装饰器。

    用户名固定 admin，密码来自 WEB_PASSWORD (.env)。
    密码为空时跳过认证（不推荐）。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        password = settings.WEB_PASSWORD
        if not password:
            # 未设密码时允许无认证访问
            return func(*args, **kwargs)

        auth = request.authorization
        if auth and auth.username == "admin" and auth.password == password:
            return func(*args, **kwargs)

        return Response(
            "需要认证",
            401,
            {"WWW-Authenticate": 'Basic realm="Roleplay Bot Web Panel"'},
        )
    return wrapper


def _ctx() -> AppContext:
    """获取当前 Flask app 的共享上下文。"""
    from flask import current_app
    return current_app.config["ctx"]


# ═══════════════════════════════════════════════════════════
# 路由注册
# ═══════════════════════════════════════════════════════════

def register_routes(app: Flask) -> None:

    @app.route("/")
    @require_auth
    def dashboard():
        ctx = _ctx()
        uptime = _format_uptime(time.time() - ctx.start_time)
        log_path = settings.LOG_FILE
        try:
            log_size = f"{log_path.stat().st_size / 1024:.0f} KB"
        except Exception:
            log_size = "N/A"
        return templates.dashboard(
            world_name=ctx.world.WORLD_NAME,
            model=settings.MODEL_NAME,
            memory_count=ctx.memory.message_count,
            long_count=ctx.memory.long_memory_count,
            npc_status=ctx.npc_manager.get_status_text(),
            uptime=uptime,
            log_size=log_size,
        )

    @app.route("/memory")
    @require_auth
    def view_short_memory():
        ctx = _ctx()
        # 只展示最近 120 条，避免页面过大
        recent = ctx.memory.memory[-120:]
        return templates.short_memory(recent, ctx.memory.message_count)

    @app.route("/memory/long", methods=["GET"])
    @require_auth
    def view_long_memory():
        ctx = _ctx()
        return templates.long_memory_cards(
            ctx.memory.long_memory,
            settings.LONG_MEMORY_MAX_ITEMS,
        )

    @app.route("/memory/long", methods=["POST"])
    @require_auth
    def add_long_memory():
        ctx = _ctx()
        content = (request.form.get("content") or "").strip()
        if not content:
            return _flash_redirect("/memory/long", "内容不能为空", "error")
        if len(content) > settings.MEMO_SIZE_LIMIT:
            return _flash_redirect("/memory/long",
                                   f"内容过长，限制 {settings.MEMO_SIZE_LIMIT} 字", "error")
        ctx.memory.add_long_memory_item(content)
        ctx.memory.save_long_memory()
        logger.info("Web panel: added long memory item")
        return _flash_redirect("/memory/long", "已写入长期记忆")

    @app.route("/memory/long/<int:index>/delete", methods=["POST"])
    @require_auth
    def delete_long_memory(index: int):
        ctx = _ctx()
        if 0 <= index < len(ctx.memory.long_memory):
            removed = ctx.memory.long_memory.pop(index)
            ctx.memory.save_long_memory()
            logger.info("Web panel: deleted long memory item: %s", removed[:50])
            return _flash_redirect("/memory/long", f"已删除: {removed[:60]}…")
        return _flash_redirect("/memory/long", "无效索引", "error")

    @app.route("/memory/long/refine", methods=["POST"])
    @require_auth
    def refine_long_memory():
        ctx = _ctx()
        try:
            # refine_long_memory 是异步方法，需要在线程中运行
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                ctx.memory.refine_long_memory(ctx.client, force=True)
            )
            loop.close()
            logger.info("Web panel: long memory refined")
            return _flash_redirect(
                "/memory/long",
                f"精炼完成，目前 {ctx.memory.long_memory_count} 条",
            )
        except Exception as exc:
            logger.error("Web panel refine failed: %s", exc)
            return _flash_redirect("/memory/long", f"精炼失败: {exc}", "error")

    @app.route("/reset", methods=["POST"])
    @require_auth
    def reset_memory():
        ctx = _ctx()
        ctx.memory.reset()
        ctx.relationship_manager.reset()
        logger.info("Web panel: memory + relationships reset for world %s", ctx.world.WORLD_NAME)
        return _flash_redirect("/", "当前世界记忆和关系网络已清空")

    # ── 世界管理 ────────────────────────────────────

    @app.route("/worlds")
    @require_auth
    def list_worlds():
        ctx = _ctx()
        worlds_dir = settings.BASE_DIR / "worlds"
        active = ctx.world.WORLD_NAME
        world_list = []
        for py_file in sorted(worlds_dir.glob("*.py")):
            name = py_file.stem
            if name == "__init__":
                continue
            size_kb = py_file.stat().st_size / 1024
            world_list.append({
                "name": name,
                "path": str(py_file.relative_to(settings.BASE_DIR)),
                "size_kb": size_kb,
                "is_active": name == active,
            })
        return templates.world_list(world_list, active)

    @app.route("/worlds/<name>", methods=["GET"])
    @require_auth
    def edit_world(name: str):
        if not name.isidentifier():
            return _flash_redirect("/worlds", "无效的世界名", "error")

        file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
        if not file_path.exists():
            return _flash_redirect("/worlds", f"世界 '{name}' 不存在", "error")

        if request.args.get("mode") == "raw":
            content = file_path.read_text(encoding="utf-8")
            return templates.world_editor_raw(
                name, content, str(file_path.relative_to(settings.BASE_DIR)),
            )

        # 表单模式：解析 .py 提取字段
        try:
            fields = _parse_world_py(file_path)
        except Exception as exc:
            logger.warning("Failed to parse world %s: %s", name, exc)
            content = file_path.read_text(encoding="utf-8")
            return templates.world_editor_raw(
                name, content, str(file_path.relative_to(settings.BASE_DIR)),
                error=f"无法解析世界文件，请使用源码模式编辑: {exc}",
            )
        return templates.world_form(
            name, fields, str(file_path.relative_to(settings.BASE_DIR)),
        )

    @app.route("/worlds/<name>", methods=["POST"])
    @require_auth
    def save_world(name: str):
        if not name.isidentifier():
            return _flash_redirect("/worlds", "无效的世界名", "error")

        file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
        if not file_path.exists():
            return _flash_redirect("/worlds", f"世界 '{name}' 不存在", "error")

        mode = request.form.get("mode", "form")

        if mode == "raw":
            content = request.form.get("content", "")
            try:
                ast.parse(content)
            except SyntaxError as exc:
                return templates.world_editor_raw(
                    name, content, str(file_path.relative_to(settings.BASE_DIR)),
                    error=f"语法错误: {exc}",
                )
            file_path.write_text(content, encoding="utf-8")
            logger.info("Web panel: saved world file %s (raw)", name)
            return _flash_redirect(f"/worlds/{name}", f"{name}.py 已保存")

        # 表单模式：收集字段值，写回 .py
        new_values: dict[str, str] = {}
        for field in ["WORLD_NAME", "START_SCENE", "SYSTEM_PROMPT",
                       "CHARACTERS", "RULES", "LOCATIONS", "EVENT_POOL", "NPCS"]:
            val = (request.form.get(f"field_{field}") or "").strip()
            # CHARACTERS, RULES, LOCATIONS, EVENT_POOL 跳过空值（保持原样）
            new_values[field] = val

        try:
            fields = _parse_world_py(file_path)
            _write_world_py(file_path, fields, new_values)
            logger.info("Web panel: saved world file %s (form)", name)
            return _flash_redirect(f"/worlds/{name}", f"{name}.py 已保存")
        except Exception as exc:
            logger.error("Failed to save world %s: %s", name, exc)
            fields_fallback = {k: v for k, v in new_values.items()}
            return templates.world_form(
                name, fields_fallback,
                str(file_path.relative_to(settings.BASE_DIR)),
                error=f"保存失败: {exc}",
            )

    @app.route("/worlds/switch", methods=["POST"])
    @require_auth
    def switch_world():
        new_world = (request.form.get("world") or "").strip()
        if not new_world.isidentifier():
            return _flash_redirect("/worlds", "无效的世界名", "error")

        file_path = settings.BASE_DIR / "worlds" / f"{new_world}.py"
        if not file_path.exists():
            return _flash_redirect("/worlds", f"世界文件 worlds/{new_world}.py 不存在", "error")

        _update_env("ACTIVE_WORLD", new_world)
        logger.info("Web panel: switched ACTIVE_WORLD to %s", new_world)
        return _flash_redirect("/worlds", f"已切换至 {new_world}，请重启 Bot 生效")

    @app.route("/restart", methods=["POST"])
    @require_auth
    def restart_bot():
        """重启 Bot 进程。

        退出码 0 配合进程管理器（tmux 下需手动重启，systemd Restart=always 会自动拉起）。
        """
        logger.warning("Web panel: restart requested")
        # 先返回响应，再退出进程（给浏览器一个交代）
        import sys
        def _do_exit():
            import time as _time
            _time.sleep(1)
            os._exit(0)
        import threading
        threading.Thread(target=_do_exit, daemon=True).start()
        return _flash_redirect("/worlds", "正在重启…")

    # ── 关系网络 ────────────────────────────────────

    @app.route("/relations", methods=["GET"])
    @require_auth
    def view_relations():
        ctx = _ctx()
        rm = ctx.relationship_manager
        if request.args.get("mode") == "raw":
            data = {
                "characters": rm.characters,
                "relations": rm.relations,
                "_reply_count_since_extract": rm._reply_count_since_extract,
            }
            return templates.relations_page_raw(
                ctx.world.WORLD_NAME, json.dumps(data, ensure_ascii=False, indent=2),
            )
        return templates.relations_page_structured(
            ctx.world.WORLD_NAME, rm.relations,
        )

    @app.route("/relations", methods=["POST"])
    @require_auth
    def save_relations():
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
                return _flash_redirect("/relations", "关系网络已保存")
            except (json.JSONDecodeError, ValueError) as exc:
                return templates.relations_page_raw(
                    ctx.world.WORLD_NAME, content, error=f"JSON 格式错误: {exc}",
                )

        # 结构化模式：从表单字段重建 relations
        new_relations: dict[str, dict] = {}
        dims = ["affection", "trust", "fear", "dependence", "suspicion", "hostility"]

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
                        rel[dim] = max(0, min(100, int(request.form.get(f"rel_{i}_{dim}", "0") or 0)))
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
                    rel[dim] = max(0, min(100, int(request.form.get(f"new_{dim}", "0") or 0)))
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
        logger.info("Web panel: saved relationships (structured) for %s", ctx.world.WORLD_NAME)
        return _flash_redirect("/relations", "关系网络已保存")

    # ── 日志 ────────────────────────────────────────

    @app.route("/logs")
    @require_auth
    def view_logs():
        log_path = settings.LOG_FILE
        if not log_path.exists():
            return templates.logs_view([], str(log_path))
        try:
            # 读取最后 100 行
            with open(log_path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            recent = all_lines[-100:]
            # 去掉行尾换行符
            lines = [line.rstrip("\n").rstrip("\r") for line in recent]
        except Exception:
            lines = ["[读取日志失败]"]
        return templates.logs_view(lines, str(log_path))


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

# 用 cookie 传递 flash 消息的简单方案：
# 重定向时在 URL 参数中带消息，页面用 JS 读取后清理 URL。
# 但为了简洁，这里用更简单的方式：在下一个请求中通过全局变量传消息。
# 实际上 Flask 有 session，但需要配置 secret_key。
# 这里用最简方案：URL query string。

def _flash_redirect(url: str, message: str = "", kind: str = "success") -> Response:
    """重定向并附带 flash 消息。"""
    from flask import redirect as _redirect
    from urllib.parse import urlencode, urlparse, urlunparse

    if not message:
        return _redirect(url)

    parts = list(urlparse(url))
    query = f"flash={kind}:{message}"
    parts[4] = query if not parts[4] else f"{parts[4]}&{query}"
    return _redirect(urlunparse(parts))


# ═══════════════════════════════════════════════════════════
# 世界文件解析与写回
# ═══════════════════════════════════════════════════════════

def _parse_world_py(path: Path) -> dict[str, str]:
    """解析世界 .py 文件，提取可编辑字段的字符串表示。

    返回: {"WORLD_NAME": "one", "START_SCENE": "(...)", ...}
    对于复杂类型（dict/list），返回格式化的文本表示。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    fields: dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
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

    return fields


def _ast_value_to_form(node) -> str:
    """将 AST 节点转换为表单可显示的字符串。"""
    if isinstance(node, ast.Constant):
        val = node.value
        if isinstance(val, str):
            return val
        return str(val)

    if isinstance(node, ast.JoinedStr):  # f-string
        # 简单场景：取字面量部分
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
            key_str = ""
            if isinstance(k, ast.Constant):
                key_str = str(k.value)
            else:
                key_str = f"({ast.dump(k)})"

            val_str = ""
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                val_str = v.value
            else:
                # 用原始源码
                try:
                    val_str = ast.get_source_segment(
                        ast.parse("").body if False else ast.Module(body=[], type_ignores=[]),
                        v,
                    ) or ""
                except Exception:
                    val_str = ""

            if val_str:
                lines.append(f"{key_str}: {val_str}")
        return "\n".join(lines)

    # 其他复杂类型：用 ast.unparse（Python 3.9+）
    try:
        return ast.unparse(node)
    except AttributeError:
        pass

    return str(ast.dump(node))


def _write_world_py(path: Path, fields: dict[str, str], new_values: dict[str, str]) -> None:
    """将表单值写回 .py 文件，替换对应顶层赋值。

    策略：按行分割原文件 → 找到每个字段的起止行 → 替换为新值 → 验证语法。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 收集每个字段的行范围
    field_ranges: dict[str, tuple[int, int]] = {}  # name → (start_line, end_line) 1-indexed
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                end = getattr(node, "end_lineno", node.lineno)
                field_ranges[target.id] = (node.lineno, end)
                break

    lines = source.splitlines()

    # 按行号降序替换（避免行号偏移）
    for name, (start, end) in sorted(field_ranges.items(), key=lambda x: -x[1][0]):
        if name not in new_values:
            continue
        new_val = new_values[name]
        if new_val == "":  # 空值跳过，保持不变
            continue

        new_lines = _format_field_assignment(name, new_val)
        lines[start - 1 : end] = new_lines

    new_source = "\n".join(lines) + "\n"

    # 验证语法
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        raise ValueError(f"生成的代码有语法错误: {exc}") from exc

    # 备份 → 写入
    backup = path.with_suffix(".py.bak")
    try:
        backup.write_text(source, encoding="utf-8")
    except Exception:
        pass
    path.write_text(new_source, encoding="utf-8")


def _format_field_assignment(name: str, value: str) -> list[str]:
    """将字段名和表单值格式化为 Python 赋值语句行列表。"""
    # 判断值的类型并选择合适的格式
    # 简单字符串（单行）→ NAME = "value"
    # 多行字符串 → NAME = ( "line1\n" "line2\n" )
    # 含冒号的行 → 可能是 dict，保持原样

    if "\n" not in value:
        # 单行值
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return [f'{name} = "{escaped}"']

    # 多行值 → 用括号包裹的三引号风格更安全
    if name in ("START_SCENE", "SYSTEM_PROMPT"):
        # 用括号 + 多行字符串字面量
        lines = [f"{name} = ("]
        for line in value.split("\n"):
            escaped = line.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    "{escaped}\\n"')
        lines.append(")")
        return lines

    if name in ("CHARACTERS", "LOCATIONS"):
        # dict 格式：每行 "key: value"
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
        # list 格式：每行一条
        lines = [f"{name}: list[str] = ["]
        for line in value.split("\n"):
            line = line.strip()
            if line:
                escaped = line.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    "{escaped}",')
        lines.append("]")
        return lines

    if name == "NPCS":
        # JSON 格式 → 保持为 dict 字面量
        try:
            import json as _json
            data = _json.loads(value)
            formatted = _json.dumps(data, ensure_ascii=False, indent=4)
            # 将 JSON 转为 Python dict 格式（简单替换）
            lines = [f"{name}: dict[str, dict] = {formatted}"]
            return lines
        except Exception:
            pass
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return [f'{name} = "{escaped}"']

    # 默认
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return [f'{name} = """{escaped}"""']
    """更新 .env 文件中的键值对，保留原有格式。"""
    env_path = settings.BASE_DIR / ".env"
    if env_path.exists():
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
    else:
        logger.warning(".env not found, cannot update %s", key)


def _format_uptime(seconds: float) -> str:
    """格式化运行时长。"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    parts.append(f"{minutes} 分钟")
    return " ".join(parts)
