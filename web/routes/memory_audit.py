"""记忆污染检查路由 — Phase 2 占位。"""

from pathlib import Path

from flask import Blueprint, render_template

from web.routes.auth import login_required
from config import settings

audit_bp = Blueprint("memory_audit", __name__, url_prefix="/memory-audit")


@audit_bp.route("/")
@login_required
def index():
    # 列出已有报告
    reports_dir = settings.BASE_DIR / "memory_audit"
    reports = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("memory-audit-*.json"), reverse=True):
            reports.append({
                "filename": f.name,
                "time": f.stem.replace("memory-audit-", ""),
                "issue_count": "?",
            })
    return render_template("memory_audit.html", reports=reports)


@audit_bp.route("/report/<filename>")
@login_required
def view_report(filename: str):
    # Phase 2 实现
    return render_template("memory_audit_report.html",
                           report={"time": filename, "issues": []})
