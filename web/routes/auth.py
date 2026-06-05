"""登录/登出路由 — Session 登录 + 失败冷却。"""

import functools
import logging
import time

from flask import Blueprint, render_template, request, redirect, url_for, session

from config import settings
from web.app import audit_log, is_ip_cooling_down, record_failed_attempt, clear_failed_attempts

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

COOLDOWN_MINUTES = 30
COOLDOWN_THRESHOLD = 3


def login_required(func):
    """装饰器：要求 session 登录。未登录跳转到登录页。"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login_page"))
        return func(*args, **kwargs)
    return wrapper


@auth_bp.route("/login", methods=["GET"])
def login_page():
    """登录页面。已登录则直接跳转仪表盘。"""
    if session.get("logged_in"):
        return redirect(url_for("dashboard.index"))

    client_ip = request.remote_addr or "unknown"
    cooldown = is_ip_cooling_down(client_ip)
    cooldown_left = 0
    if cooldown:
        # 计算剩余冷却时间
        from web.app import _failed_attempts
        attempts = _failed_attempts.get(client_ip, [])
        if attempts:
            oldest = min(attempts)
            remaining = COOLDOWN_MINUTES * 60 - (time.time() - oldest)
            cooldown_left = max(1, int(remaining // 60))

    return render_template("login.html", error=None, cooldown=cooldown_left)


@auth_bp.route("/login", methods=["POST"])
def login():
    """处理登录表单提交。"""
    client_ip = request.remote_addr or "unknown"

    # 检查冷却
    if is_ip_cooling_down(client_ip):
        return render_template("login.html", error="登录失败次数过多，请稍后再试。", cooldown=COOLDOWN_MINUTES)

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "")

    # 验证
    if not settings.WEB_PASSWORD:
        # 未设密码时允许无密码登录
        session["logged_in"] = True
        session["login_time"] = time.time()
        audit_log("登录成功", f"无密码模式, IP={client_ip}")
        return redirect(url_for("dashboard.index"))

    if username == "admin" and password == settings.WEB_PASSWORD:
        session["logged_in"] = True
        session["login_time"] = time.time()
        clear_failed_attempts(client_ip)
        audit_log("登录成功", f"IP={client_ip}")
        logger.info("Web panel: admin login from %s", client_ip)
        return redirect(url_for("dashboard.index"))

    # 登录失败
    record_failed_attempt(client_ip)
    audit_log("登录失败", f"IP={client_ip}", success=False)
    logger.warning("Web panel: failed login from %s", client_ip)

    from web.app import _failed_attempts
    attempts = _failed_attempts.get(client_ip, [])
    remaining = COOLDOWN_THRESHOLD - len(attempts)
    error = f"用户名或密码错误。还剩 {remaining} 次机会。" if remaining > 0 else "已锁定，请等待 30 分钟。"

    return render_template("login.html", error=error, cooldown=0)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """登出。"""
    audit_log("登出", f"IP={request.remote_addr}")
    session.clear()
    return redirect(url_for("auth.login_page"))
