"""Flask Web 管理面板 — AI 角色扮演导演台。

Session 登录 + CSRF 保护 + 安全响应头 + 操作审计日志。
所有敏感信息不暴露，系统级操作不提供。
"""

import logging
import secrets
import time
from datetime import datetime

from flask import Flask, session, request, redirect, url_for

from config import settings

logger = logging.getLogger(__name__)

# ── 审计日志 ──────────────────────────────────────────

AUDIT_LOG_PATH = settings.BASE_DIR / "web_audit.log"


def audit_log(action: str, detail: str = "", success: bool = True) -> None:
    """写入操作审计日志。"""
    status = "SUCCESS" if success else "FAILURE"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{status}] {action} | {detail}\n")
    except Exception:
        pass


# ── 登录失败冷却追踪 ──────────────────────────────────

_failed_attempts: dict[str, list[float]] = {}

COOLDOWN_THRESHOLD = 3
COOLDOWN_MINUTES = 30


def is_ip_cooling_down(ip: str) -> bool:
    """检查 IP 是否在冷却期。"""
    attempts = _failed_attempts.get(ip, [])
    now = time.time()
    recent = [t for t in attempts if now - t < COOLDOWN_MINUTES * 60]
    _failed_attempts[ip] = recent
    return len(recent) >= COOLDOWN_THRESHOLD


def record_failed_attempt(ip: str) -> None:
    """记录一次登录失败。"""
    attempts = _failed_attempts.get(ip, [])
    now = time.time()
    attempts.append(now)
    _failed_attempts[ip] = [t for t in attempts if now - t < COOLDOWN_MINUTES * 60]


def clear_failed_attempts(ip: str) -> None:
    """清除 IP 的失败记录。"""
    _failed_attempts.pop(ip, None)


# ── App 上下文 ────────────────────────────────────────


class AppContext:
    """Flask 与 Bot 共享的状态容器。"""

    def __init__(self, world_manager, roleplay_bot, client, start_time: float):
        self._world_manager = world_manager
        self._roleplay_bot = roleplay_bot
        self.client = client
        self.start_time = start_time
        self.telegram_polling_started = False
        # 存储最近一次上下文选择结果（供调试面板使用）
        self.last_selection: dict | None = None

    @property
    def world(self):
        """当前激活的世界（从 WorldManager 动态获取）。"""
        return self._world_manager.get_world()

    @property
    def world_manager(self):
        return self._world_manager

    @property
    def memory(self):
        return self._roleplay_bot.memory

    @property
    def npc_manager(self):
        return self._roleplay_bot.npc_manager

    @property
    def relationship_manager(self):
        return self._roleplay_bot.relationship_manager

    @property
    def time_manager(self):
        return self._roleplay_bot.time_manager
    
    @property
    def roleplay_bot(self):
        return self._roleplay_bot


def _ctx():
    """获取当前 Flask app 的共享上下文。"""
    from flask import current_app
    return current_app.config["ctx"]


# ── Flask 工厂 ────────────────────────────────────────


def create_app(ctx: AppContext) -> Flask:
    """创建 Flask 应用并配置安全策略。"""
    app = Flask(__name__)
    app.config["ctx"] = ctx

    # Session 签名密钥必须与登录密码独立，避免 Cookie 成为离线猜密码的校验器。
    if settings.WEB_SESSION_SECRET:
        app.secret_key = settings.WEB_SESSION_SECRET
    else:
        # 仅允许本地开发回退；公网监听由 main.validate_settings() 拒绝启动。
        app.secret_key = secrets.token_hex(32)
        logger.warning("WEB_SESSION_SECRET is not set; web sessions will be invalidated on restart")

    # Session Cookie 安全配置
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 如果通过 HTTPS 反代访问，应设置 secure=True
    # 默认为 False 以兼容 HTTP 直连；HTTPS 反代用户可在 .env 中配置
    app.config["SESSION_COOKIE_SECURE"] = os_env_bool("SESSION_COOKIE_SECURE", False)

    # ── 安全响应头 ──
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # CSP: 只允许本站资源，style 允许 unsafe-inline（Jinja2 内联样式）
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'"
        )
        return response

    # ── CSRF 检查（before_request）──
    @app.before_request
    def csrf_check():
        """对所有 POST/PUT/DELETE 请求检查 CSRF token。
        登录页面除外（未登录时没有 session token）。
        """
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return None

        # 登录路由不需要 CSRF（还没有 session）
        if request.path == url_for("auth.login") or request.path == url_for("auth.login_page"):
            return None

        token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
        expected = session.get("_csrf_token")
        if not token or not expected or not secrets.compare_digest(token, expected):
            logger.warning("CSRF validation failed from %s on %s", request.remote_addr, request.path)
            return "CSRF validation failed", 403

        return None

    # ── 注册蓝图 ──
    from web.routes.auth import auth_bp
    from web.routes.dashboard import dashboard_bp
    from web.routes.config_center import config_bp
    from web.routes.worlds import worlds_bp
    from web.routes.memory import memory_bp
    from web.routes.memory_audit import audit_bp
    from web.routes.relations import relations_bp
    from web.routes.time_routes import time_bp
    from web.routes.logs import logs_bp
    from web.routes.providers import providers_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(worlds_bp)
    app.register_blueprint(memory_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(relations_bp)
    app.register_blueprint(time_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(providers_bp)

    # ── CSRF Token 注入模板全局变量 ──
    @app.context_processor
    def inject_csrf():
        """在所有模板中可用 {{ csrf_token() }}。"""
        def _generate():
            if "_csrf_token" not in session:
                session["_csrf_token"] = secrets.token_hex(32)
            return session["_csrf_token"]
        return {"csrf_token": _generate}

    # ── 全局模板变量 ──
    @app.context_processor
    def inject_globals():
        """在所有模板中注入 ctx 和 uptime。"""
        from flask import current_app
        ctx = current_app.config.get("ctx")
        if ctx:
            uptime = _format_uptime(time.time() - ctx.start_time)
        else:
            uptime = "--"
        return {"ctx": ctx, "uptime": uptime}

    # ── 404 处理 ──
    @app.errorhandler(404)
    def not_found(e):
        if session.get("logged_in"):
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login_page"))

    return app


# ── Flash 消息辅助 ────────────────────────────────────

def _flash_redirect(url: str, message: str = "", kind: str = "success"):
    """重定向并附带 flash 消息（通过 URL query 参数）。"""
    from flask import redirect as _redirect
    from urllib.parse import urlencode, urlparse, urlunparse, quote

    if not message:
        return _redirect(url)

    parts = list(urlparse(url))
    query_parts = []
    if parts[4]:
        query_parts.append(parts[4])
    query_parts.append(f"flash_kind={kind}&flash_msg={quote(message)}")
    parts[4] = "&".join(query_parts)
    return _redirect(urlunparse(parts))


# ── 工具函数 ──────────────────────────────────────────

def os_env_bool(key: str, default: bool = False) -> bool:
    """读取环境变量的布尔值（Flask 配置用）。"""
    import os
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes", "on")


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
