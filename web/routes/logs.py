"""日志查看路由。"""

import re

from flask import Blueprint, render_template

from config import settings
from web.app import _ctx
from web.routes.auth import login_required

logs_bp = Blueprint("logs", __name__, url_prefix="/logs")

# 敏感信息过滤正则
_SENSITIVE_PATTERNS = [
    (re.compile(r'(BOT_TOKEN|DEEPSEEK_KEY|WEB_PASSWORD)\s*=\s*\S+', re.I), r'\1=***'),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), 'sk-***'),
    (re.compile(r'\d{8,10}:[a-zA-Z0-9_-]{30,}'), '***:***'),
]


def _filter_sensitive(line: str) -> str:
    """过滤敏感信息。"""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        line = pattern.sub(replacement, line)
    return line


@logs_bp.route("/")
@login_required
def index():
    ctx = _ctx()
    log_path = settings.LOG_FILE
    if not log_path.exists():
        return render_template("logs.html", lines=[], log_path=str(log_path), ctx=ctx)

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        recent = all_lines[-100:]
        lines = [_filter_sensitive(line.rstrip("\n").rstrip("\r")) for line in recent]
    except Exception:
        lines = ["[读取日志失败]"]

    return render_template("logs.html", lines=lines, log_path=str(log_path), ctx=ctx)
