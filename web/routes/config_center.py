"""配置中心路由 — Phase 2 占位。"""

from flask import Blueprint, render_template

from web.routes.auth import login_required

config_bp = Blueprint("config_center", __name__, url_prefix="/config")


@config_bp.route("/")
@login_required
def index():
    return render_template("config.html")
