"""运行状态路径与旧目录兼容迁移。"""

import json
import logging
from pathlib import Path

from bot.safe_io import atomic_write_json
from config import settings


logger = logging.getLogger(__name__)


def state_path(world_name: str, state_name: str) -> Path:
    """返回状态文件路径；首次使用时保留式迁移旧 ``memory/`` JSON。"""
    target = settings.STATE_DIR / f"{world_name}_{state_name}.json"
    legacy = settings.LEGACY_STATE_DIR / target.name
    if target.exists() or not legacy.exists():
        return target

    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("状态文件根节点必须是对象")
        if atomic_write_json(target, data, backup=False):
            logger.info("Migrated legacy state %s -> %s; legacy file kept", legacy, target)
            return target
    except Exception as exc:
        logger.warning("Unable to migrate legacy state %s: %s; continuing to use it", legacy, exc)
    return legacy
