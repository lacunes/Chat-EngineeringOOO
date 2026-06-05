from web.routes.auth import auth_bp
from web.routes.dashboard import dashboard_bp
from web.routes.config_center import config_bp
from web.routes.worlds import worlds_bp
from web.routes.memory import memory_bp
from web.routes.memory_audit import audit_bp
from web.routes.relations import relations_bp
from web.routes.time_routes import time_bp
from web.routes.logs import logs_bp

__all__ = [
    "auth_bp", "dashboard_bp", "config_bp", "worlds_bp",
    "memory_bp", "audit_bp", "relations_bp", "time_bp", "logs_bp",
]
