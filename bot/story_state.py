"""轻量剧情状态管理器。

管理当前世界的剧情阶段信息（章节/场景/冲突/目标/节奏等）。
纯本地 JSON 文件，不调用 API。文件不存在时静默跳过。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("bot.story")

DEFAULT_STORY_STATE = {
    "chapter": "",
    "scene": "",
    "location": "",
    "active_characters": [],
    "current_conflict": "",
    "current_goal": "",
    "pacing": "normal",
    "allowed_events": [],
    "forbidden_events": [],
    "last_major_event": "",
    "notes": "",
}


class StoryStateManager:
    """管理当前世界的剧情状态（story_state.json）。"""

    def __init__(self, world_name: str, memory_dir: Path):
        self.file_path = memory_dir / f"{world_name}_story_state.json"
        self.state: dict = dict(DEFAULT_STORY_STATE)
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            logger.info("No story state file for world — using defaults")
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            # 合并默认值，防止新增字段缺失
            merged = dict(DEFAULT_STORY_STATE)
            merged.update({k: v for k, v in data.items() if k in DEFAULT_STORY_STATE})
            self.state = merged
            active = ", ".join(self.state.get("active_characters", [])) or "(未设置)"
            logger.info(
                "Loaded story state: chapter='%s', scene='%s', pacing=%s, active=[%s]",
                self.state.get("chapter", ""),
                self.state.get("scene", ""),
                self.state.get("pacing", "normal"),
                active,
            )
        except Exception as exc:
            logger.error("Failed to load story state: %s", exc)

    def save(self) -> None:
        from bot.safe_io import atomic_write_json
        atomic_write_json(self.file_path, self.state)

    def update(self, updates: dict) -> None:
        """批量更新状态字段。只接受已知字段。"""
        for key, value in updates.items():
            if key in DEFAULT_STORY_STATE:
                self.state[key] = value
        self.save()

    def get_summary(self) -> str:
        """生成注入 dynamic_state 的剧情状态摘要。如果全为空则返回空字符串。"""
        parts = []
        s = self.state

        if s.get("chapter"):
            parts.append(f"章节：{s['chapter']}")
        if s.get("scene"):
            parts.append(f"场景：{s['scene']}")
        if s.get("location"):
            parts.append(f"地点：{s['location']}")
        if s.get("active_characters"):
            parts.append(f"在场角色：{', '.join(s['active_characters'])}")
        if s.get("current_conflict"):
            parts.append(f"当前冲突：{s['current_conflict']}")
        if s.get("current_goal"):
            parts.append(f"当前目标：{s['current_goal']}")
        if s.get("pacing") and s["pacing"] != "normal":
            parts.append(f"节奏：{s['pacing']}")
        if s.get("forbidden_events"):
            parts.append(f"禁止事件：{', '.join(s['forbidden_events'])}")

        if not parts:
            return ""

        return "[当前剧情状态]\n" + "\n".join(parts)

    def is_event_allowed(self, event_text: str) -> bool:
        """检查事件是否违反 forbidden_events。"""
        forbidden = [e.lower() for e in self.state.get("forbidden_events", []) if e.strip()]
        if not forbidden:
            return True
        text_lower = event_text.lower()
        for pattern in forbidden:
            if pattern in text_lower:
                return False
        return True

    def get_event_boost(self, event_text: str) -> float:
        """如果事件匹配 allowed_events，返回提升系数；否则返回 1.0。"""
        allowed = [e.lower() for e in self.state.get("allowed_events", []) if e.strip()]
        if not allowed:
            return 1.0
        text_lower = event_text.lower()
        for pattern in allowed:
            if pattern in text_lower:
                return 1.5  # 提升 50%
        return 1.0
