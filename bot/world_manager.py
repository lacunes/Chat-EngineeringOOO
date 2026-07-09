"""
WorldManager — 世界数据热加载与切换。

职责：
1. 从 data/worlds/*.yaml 加载世界数据（JSON 兜底）
2. 管理 active_world（优先读取 data/runtime_state.json）
3. 支持运行时切换世界（无需重启 main.py）
4. 支持热重载当前世界（Web 编辑 YAML 后自动生效）
5. 不再支持 worlds/*.py 旧格式
"""

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import yaml

from config import settings

logger = logging.getLogger(__name__)

# 线程锁
_lock = threading.RLock()


def _runtime_state_path() -> Path:
    return settings.BASE_DIR / "data" / "runtime_state.json"


def _load_runtime_state() -> dict:
    """读取 runtime_state.json，不存在则创建默认。"""
    path = _runtime_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read runtime_state.json, using default")
    return {}


def _save_runtime_state(state: dict) -> None:
    """原子保存 runtime_state.json。"""
    from bot.safe_io import atomic_write_json
    path = _runtime_state_path()
    with _lock:
        atomic_write_json(path, state)


class WorldManager:
    """世界数据管理器。

    使用方式：
        wm = WorldManager()
        world = wm.get_world()           # 获取当前世界
        wm.switch_world("two")           # 切换世界
        wm.reload_world()                # 热重载（YAML 被编辑后）
    """

    def __init__(self):
        self._world: Optional[SimpleNamespace] = None
        self._world_name: str = ""
        self._world_mtime: float = 0.0  # 文件修改时间，用于热重载检测

    # ── 获取当前世界 ──

    def get_world(self) -> SimpleNamespace:
        """获取当前激活的世界。

        首次调用时从 runtime_state.json（或 .env 兜底）加载。
        后续调用会检查 YAML 文件是否被修改并自动热重载。
        """
        current_name = self._resolve_active_world()

        # 如果世界名变了（Web 切换），重新加载
        if current_name != self._world_name:
            self._load_world(current_name)
        elif self._world is not None:
            # 同世界：检查文件是否被修改（热重载）
            self._check_reload()

        if self._world is None:
            self._load_world(current_name)

        return self._world

    @property
    def world_name(self) -> str:
        """当前世界名。"""
        if not self._world_name:
            self._world_name = self._resolve_active_world()
        return self._world_name

    # ── 切换世界 ──

    def switch_world(self, name: str) -> bool:
        """切换到指定世界。返回是否成功。"""
        safe_name = name.strip().lower()
        if not safe_name.isidentifier():
            logger.warning("Invalid world name: %s", name)
            return False

        # 检查世界文件是否存在
        yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{safe_name}.yaml"
        json_path = settings.BASE_DIR / "data" / "worlds" / f"{safe_name}.json"
        if not yaml_path.exists() and not json_path.exists():
            logger.warning("World '%s' not found", safe_name)
            return False

        # 写入 runtime_state.json
        state = _load_runtime_state()
        state["active_world"] = safe_name
        _save_runtime_state(state)

        # 立即加载
        self._load_world(safe_name)
        logger.info("Switched to world: %s", safe_name)
        return True

    # ── 热重载 ──

    def reload_world(self) -> bool:
        """强制重新加载当前世界（Web 编辑 YAML 后调用）。返回是否成功。"""
        if not self._world_name:
            return False
        self._load_world(self._world_name)
        return True

    def _check_reload(self) -> None:
        """检查当前 YAML 文件是否被修改，是则自动重载。"""
        yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{self._world_name}.yaml"
        if not yaml_path.exists():
            return
        try:
            mtime = yaml_path.stat().st_mtime
            if mtime != self._world_mtime:
                logger.info("World YAML changed, hot-reloading %s...", self._world_name)
                self._load_world(self._world_name)
        except Exception:
            pass

    # ── 内部 ──

    def _resolve_active_world(self) -> str:
        """解析当前应激活的世界名。

        优先级：runtime_state.json > .env ACTIVE_WORLD > "one"。
        """
        state = _load_runtime_state()
        name = state.get("active_world", "").strip().lower()
        if name and name.isidentifier():
            return name
        # 兜底 .env
        name = settings.ACTIVE_WORLD
        if name and name.isidentifier():
            return name
        return "one"

    def _load_world(self, name: str) -> None:
        """加载指定世界的数据。"""
        from bot.utils import load_world as _load
        try:
            world = _load(name)
            self._world = world
            self._world_name = name

            # 记录文件修改时间
            yaml_path = settings.BASE_DIR / "data" / "worlds" / f"{name}.yaml"
            if yaml_path.exists():
                self._world_mtime = yaml_path.stat().st_mtime
            else:
                self._world_mtime = 0.0

            logger.info("Loaded world: %s", name)
        except Exception as exc:
            logger.error("Failed to load world '%s': %s", name, exc)
            if self._world is None:
                raise  # 首次加载失败则抛出
            # 非首次：保留旧世界，只记录错误
