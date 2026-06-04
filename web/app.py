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
        return templates.long_memory(
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
        logger.info("Web panel: memory reset for world %s", ctx.world.WORLD_NAME)
        return _flash_redirect("/", "当前世界记忆已清空")

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
        # 安全检查：防止路径穿越
        if not name.isidentifier():
            return _flash_redirect("/worlds", "无效的世界名", "error")

        file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
        if not file_path.exists():
            return _flash_redirect("/worlds", f"世界 '{name}' 不存在", "error")

        content = file_path.read_text(encoding="utf-8")
        return templates.world_editor(name, content, str(file_path.relative_to(settings.BASE_DIR)))

    @app.route("/worlds/<name>", methods=["POST"])
    @require_auth
    def save_world(name: str):
        if not name.isidentifier():
            return _flash_redirect("/worlds", "无效的世界名", "error")

        file_path = settings.BASE_DIR / "worlds" / f"{name}.py"
        if not file_path.exists():
            return _flash_redirect("/worlds", f"世界 '{name}' 不存在", "error")

        content = request.form.get("content", "")
        # 检查 Python 语法，防止保存后 Bot 启动失败
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return templates.world_editor(
                name, content, str(file_path.relative_to(settings.BASE_DIR)),
                error=f"语法错误: {exc}",
            )

        file_path.write_text(content, encoding="utf-8")
        logger.info("Web panel: saved world file %s", name)
        return _flash_redirect(f"/worlds/{name}", f"{name}.py 已保存")

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
        data = {
            "characters": rm.characters,
            "relations": rm.relations,
            "_reply_count_since_extract": rm._reply_count_since_extract,
        }
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        return templates.relations_page(ctx.world.WORLD_NAME, json_text)

    @app.route("/relations", methods=["POST"])
    @require_auth
    def save_relations():
        ctx = _ctx()
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
            logger.info("Web panel: saved relationships for %s", ctx.world.WORLD_NAME)
            return _flash_redirect("/relations", "关系网络已保存")
        except (json.JSONDecodeError, ValueError) as exc:
            return templates.relations_page(
                ctx.world.WORLD_NAME, content, error=f"JSON 格式错误: {exc}",
            )

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


def _update_env(key: str, value: str) -> None:
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
