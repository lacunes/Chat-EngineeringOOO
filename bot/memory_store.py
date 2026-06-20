"""
memory_store — 结构化长期记忆数据模型。

从吸收迭代 v3 开始，长期记忆不再是无类型的扁平字符串列表，
而是包含 id、world_id、type、participants、importance、status 等字段的结构化记录。

兼容旧格式（分类标签前缀的字符串列表），自动迁移。
"""

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bot.memory")

# ── 记忆类型 ──
MEMORY_TYPES = frozenset({
    "fact",           # 稳定事实（身份、习惯、背景、已确认设定）
    "relationship",   # 角色间关系变化
    "event",          # 已发生的重要剧情事件
    "promise",        # 角色间承诺、约定和未完成事项
    "preference",     # 用户或角色的稳定偏好
    "secret",         # 只有部分角色知道的信息
    "goal",           # 角色当前/长期/待完成目标
    "scene_state",    # 需跨会话保留的场景状态
})

# ── 记忆生命周期状态 ──
LIFECYCLE_STATUSES = frozenset({
    "active",         # 有效，可被召回
    "resolved",       # 已解决（如完成的承诺）
    "superseded",     # 被新事实替代
    "archived",       # 已归档
    "deleted",        # 人工删除（不物理删除，标记）
})

# ── 旧格式分类标签 → 新类型映射 ──
OLD_CATEGORY_TO_TYPE: dict[str, str] = {
    "hard_fact":        "fact",
    "relationship":     "relationship",
    "plot_fact":        "event",
    "user_preference":  "preference",
    "temporary_state":  "scene_state",
    "character_state":  "fact",
    "world_state":      "scene_state",
    "legacy":           "fact",
}

_OLD_CATEGORY_RE = re.compile(r"^\[([a-z_]+)\]\s*")


@dataclass
class MemoryItem:
    """单条结构化长期记忆记录。

    所有字段均提供默认值，确保旧数据迁移时不丢失信息。
    """

    id: str = ""                              # 唯一 ID（mem_xxx）
    world_id: str = ""                        # 所属世界
    type: str = "fact"                        # 记忆类型
    content: str = ""                         # 记忆文本内容
    participants: list[str] = field(default_factory=list)   # 相关角色
    importance: float = 0.5                   # 重要度 0.0~1.0
    confidence: float = 0.8                   # 置信度 0.0~1.0
    status: str = "active"                    # 生命周期状态
    tags: list[str] = field(default_factory=list)          # 标签
    source_message_ids: list[str] = field(default_factory=list)  # 来源消息
    created_at: str = ""                      # ISO 时间戳
    updated_at: str = ""                      # ISO 时间戳
    last_recalled_at: str = ""                # 最后召回时间
    recall_count: int = 0                     # 召回次数
    expires_at: str = ""                      # 过期时间（空=永不过期）
    # ── 承诺/誓约扩展字段 ──
    promise_from: str = ""                    # 谁对谁承诺（from）
    promise_to: str = ""                      # 谁对谁承诺（to）
    promise_status: str = ""                  # active/fulfilled/broken
    promise_linked_event: str = ""            # 关联剧情事件 ID


# ═══════════════════════════════════════════════════════════════
# MemoryStore — 结构化记忆持久化
# ═══════════════════════════════════════════════════════════════

class MemoryStore:
    """管理一个世界的结构化长期记忆。

    文件路径：data/memory/{world_id}_memories.json（v3 新格式）
    旧格式：data/memory/{world_id}_long_term.json（自动迁移）
    """

    def __init__(self, world_id: str, base_dir: Path):
        self.world_id = world_id
        self._base_dir = base_dir
        self._data_dir = base_dir / "data" / "memory"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 文件路径
        self._new_path = self._data_dir / f"{world_id}_memories.json"
        self._old_path = self._data_dir / f"{world_id}_long_term.json"
        # 极旧路径（v1）
        self._very_old_path = base_dir / "memory" / f"{world_id}_world_memory.json"

        self._lock = threading.Lock()
        self._items: dict[str, MemoryItem] = {}  # id → MemoryItem

        self._load()

        # 一次性清理：将旧的多条 active scene_state 中非最新的标记为 superseded
        cleaned = self._cleanup_duplicate_scene_states()
        if cleaned:
            self._save()
            logger.info("Cleaned up %d duplicate scene_state(s) from legacy data", cleaned)

    # ── 属性 ──

    @property
    def count(self) -> int:
        """活跃记忆数量（不含 deleted/archived）。"""
        return sum(1 for m in self._items.values() if m.status in ("active",))

    @property
    def total_count(self) -> int:
        """包括所有状态的记忆数量。"""
        return len(self._items)

    def active_items(self) -> list[MemoryItem]:
        """返回所有 active 状态的记忆，按重要性降序。"""
        items = [m for m in self._items.values() if m.status == "active"]
        items.sort(key=lambda m: m.importance, reverse=True)
        return items

    def get(self, mem_id: str) -> Optional[MemoryItem]:
        return self._items.get(mem_id)

    # ── CRUD ──

    def add(self, item: MemoryItem) -> str:
        """添加一条记忆。如果 content 与已有记忆高度相似则合并。返回 id。

        scene_state 类型采用 upsert 语义：添加前自动将已有 active scene_state
        标记为 superseded，确保同一时刻只有一条当前场景状态。

        relationship 类型由 RelationshipManager 独立管理，此处拒绝写入。
        """
        if not item.id:
            item.id = _new_id()
        if not item.world_id:
            item.world_id = self.world_id
        if not item.created_at:
            now = _now()
            item.created_at = now
            item.updated_at = now
        if item.type not in MEMORY_TYPES:
            item.type = "fact"

        # ── relationship 由 RelationshipManager 独立管理 ──
        if item.type == "relationship":
            logger.warning(
                "Rejected relationship memory write (handled by RelationshipManager): %s",
                item.content[:100],
            )
            return ""  # 返回空 id 表示拒绝

        if item.status not in LIFECYCLE_STATUSES:
            item.status = "active"

        with self._lock:
            # ── scene_state upsert：新状态替代旧状态 ──
            if item.type == "scene_state":
                self._supersede_scene_states()

            # 检查是否有可合并的已有记忆
            merged = self._try_merge(item)
            if merged:
                return merged
            self._items[item.id] = item
        self._save()
        return item.id

    def update(self, mem_id: str, **kwargs) -> bool:
        """更新记忆字段。只允许修改已知字段。"""
        with self._lock:
            item = self._items.get(mem_id)
            if not item:
                return False
            for key, value in kwargs.items():
                if hasattr(item, key):
                    setattr(item, key, value)
            item.updated_at = _now()
        self._save()
        return True

    def set_status(self, mem_id: str, status: str) -> bool:
        """修改记忆生命周期状态。"""
        if status not in LIFECYCLE_STATUSES:
            return False
        return self.update(mem_id, status=status, updated_at=_now())

    def delete(self, mem_id: str) -> bool:
        """软删除（标记为 deleted，不物理删除）。"""
        return self.set_status(mem_id, "deleted")

    def archive(self, mem_id: str) -> bool:
        """归档记忆。"""
        return self.set_status(mem_id, "archived")

    def record_recall(self, mem_id: str) -> None:
        """记录一次召回。"""
        with self._lock:
            item = self._items.get(mem_id)
            if item:
                item.recall_count += 1
                item.last_recalled_at = _now()
        # 不立即保存，调用方批量 save

    def resolve_promise(self, mem_id: str, outcome: str = "fulfilled") -> bool:
        """将承诺标记为已履行或已违背。"""
        with self._lock:
            item = self._items.get(mem_id)
            if not item or item.type != "promise":
                return False
            item.promise_status = outcome
            item.status = "resolved"
            item.updated_at = _now()
        self._save()
        return True

    # ── 查询 ──

    def query(
        self,
        types: list[str] | None = None,
        participants: list[str] | None = None,
        status: str = "active",
        tags: list[str] | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """按条件筛选记忆。"""
        result = []
        for item in self._items.values():
            if status and item.status != status:
                continue
            if types and item.type not in types:
                continue
            if min_importance > 0 and item.importance < min_importance:
                continue
            if participants:
                if not any(p in item.participants for p in participants):
                    continue
            if tags:
                if not any(t in item.tags for t in tags):
                    continue
            result.append(item)
        result.sort(key=lambda m: m.importance, reverse=True)
        return result[:limit]

    def get_promises(self, status_filter: str = "active") -> list[MemoryItem]:
        """获取所有承诺。"""
        return [m for m in self._items.values()
                if m.type == "promise" and m.status == status_filter]

    def to_text_list(self, limit: int = 20) -> list[str]:
        """转换为旧格式兼容的文本列表（供 prompt 注入使用）。"""
        items = self.active_items()[:limit]
        result = []
        for item in items:
            line = f"[{item.type}] {item.content}"
            if item.participants:
                line += f"（涉及：{'、'.join(item.participants)}）"
            result.append(line)
        return result

    def save(self) -> None:
        self._save()

    # ══════════════════════════════════════════════════════════
    # 内部
    # ══════════════════════════════════════════════════════════

    def _load(self) -> None:
        """加载记忆文件，优先新格式，自动迁移旧格式。"""
        # 1. 尝试新格式
        if self._new_path.exists():
            loaded = self._load_new_format()
            if loaded:
                return

        # 2. 尝试旧格式迁移
        if self._old_path.exists():
            self._migrate_old_format(self._old_path)
            return

        # 3. 尝试极旧路径
        if self._very_old_path.exists():
            self._migrate_old_format(self._very_old_path)
            return

        logger.info("No memory file for '%s', starting fresh", self.world_id)

    def _load_new_format(self) -> bool:
        """加载 v3 JSON 格式。失败时返回 False（保留原文件，不覆盖）。"""
        try:
            text = self._new_path.read_text(encoding="utf-8")
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "Failed to parse %s: %s — file preserved, using empty store",
                self._new_path, exc,
            )
            # 创建损坏备份
            self._backup_corrupted(text)
            return False

        if not isinstance(data, list):
            logger.warning("%s is not a list, using empty store", self._new_path)
            return False

        count = 0
        for raw in data:
            if not isinstance(raw, dict):
                continue
            try:
                item = MemoryItem(
                    id=raw.get("id", _new_id()),
                    world_id=raw.get("world_id", self.world_id),
                    type=raw.get("type", "fact"),
                    content=raw.get("content", ""),
                    participants=raw.get("participants", []),
                    importance=float(raw.get("importance", 0.5)),
                    confidence=float(raw.get("confidence", 0.8)),
                    status=raw.get("status", "active"),
                    tags=raw.get("tags", []),
                    source_message_ids=raw.get("source_message_ids", []),
                    created_at=raw.get("created_at", ""),
                    updated_at=raw.get("updated_at", ""),
                    last_recalled_at=raw.get("last_recalled_at", ""),
                    recall_count=int(raw.get("recall_count", 0)),
                    expires_at=raw.get("expires_at", ""),
                    promise_from=raw.get("promise_from", ""),
                    promise_to=raw.get("promise_to", ""),
                    promise_status=raw.get("promise_status", ""),
                    promise_linked_event=raw.get("promise_linked_event", ""),
                )
                self._items[item.id] = item
                count += 1
            except Exception as exc:
                logger.warning("Skipping invalid memory item: %s", exc)

        logger.info("Loaded %d memories for '%s' from %s", count, self.world_id, self._new_path)
        return True

    def _migrate_old_format(self, old_path: Path) -> None:
        """从旧格式（扁平字符串列表）迁移到新结构化格式。迁移前自动备份旧文件。"""
        logger.info("Migrating old memory format: %s", old_path)

        # 备份旧文件
        self._backup_old_file(old_path)

        try:
            data = json.loads(old_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load old memory file %s: %s", old_path, exc)
            return

        if not isinstance(data, list):
            logger.warning("Old memory file %s is not a list", old_path)
            return

        migrated = 0
        for text in data:
            if not isinstance(text, str) or not text.strip():
                continue

            # 解析旧分类标签
            item_type = "fact"
            content = text.strip()
            m = _OLD_CATEGORY_RE.match(content)
            if m:
                cat = m.group(1)
                item_type = OLD_CATEGORY_TO_TYPE.get(cat, "fact")
                content = content[m.end():].strip()

            item = MemoryItem(
                id=_new_id(),
                world_id=self.world_id,
                type=item_type,
                content=content,
                importance=0.5,
                confidence=0.8,
                status="active",
                created_at=_now(),
                updated_at=_now(),
            )
            self._items[item.id] = item
            migrated += 1

        # 保存新格式
        self._save()
        logger.info("Migrated %d memories for '%s' from old format", migrated, self.world_id)

    def _save(self) -> None:
        """原子保存到新格式 JSON。"""
        with self._lock:
            data = [asdict(item) for item in self._items.values()]
            self._atomic_write_json(self._new_path, data)

    def _supersede_scene_states(self) -> int:
        """将所有 active scene_state 标记为 superseded。返回受影响的数量。"""
        count = 0
        for item in self._items.values():
            if item.type == "scene_state" and item.status == "active":
                item.status = "superseded"
                item.updated_at = _now()
                count += 1
        if count:
            logger.debug("Superseded %d old scene_state(s)", count)
        return count

    def _cleanup_duplicate_scene_states(self) -> int:
        """如果存在多条 active scene_state，只保留最新的一条（按 created_at 排序），
        其余标记为 superseded。用于清理 Phase 2 之前的遗留数据。
        返回被清理的数量。
        """
        active_scenes = [
            item for item in self._items.values()
            if item.type == "scene_state" and item.status == "active"
        ]
        if len(active_scenes) <= 1:
            return 0

        # 按创建时间排序，保留最新的
        active_scenes.sort(key=lambda m: m.created_at or "")
        # 最新的保留 active，其余标记 superseded
        for item in active_scenes[:-1]:
            item.status = "superseded"
            item.updated_at = _now()
        logger.info(
            "Cleanup: %d duplicate scene_state(s) → %d superseded, 1 kept active",
            len(active_scenes), len(active_scenes) - 1,
        )
        return len(active_scenes) - 1

    def _try_merge(self, new_item: MemoryItem) -> Optional[str]:
        """检查是否有可合并的已有记忆（content 完全相同或高度相似）。
        返回已合并到的已有 id，或 None 表示不合并。
        """
        new_content = new_item.content.strip()
        for existing in self._items.values():
            if existing.status == "deleted":
                continue
            if existing.content.strip() == new_content:
                # 完全相同：更新已有记忆
                existing.importance = max(existing.importance, new_item.importance)
                existing.confidence = max(existing.confidence, new_item.confidence)
                existing.updated_at = _now()
                existing.tags = list(set(existing.tags + new_item.tags))
                existing.participants = list(set(existing.participants + new_item.participants))
                logger.debug("Merged duplicate memory into %s", existing.id)
                return existing.id
            # 高度相似（一方包含另一方）
            if len(new_content) > 10 and len(existing.content.strip()) > 10:
                if new_content in existing.content.strip() or existing.content.strip() in new_content:
                    # 保留较长的版本
                    if len(new_content) > len(existing.content.strip()):
                        existing.content = new_content
                    existing.importance = max(existing.importance, new_item.importance)
                    existing.updated_at = _now()
                    existing.tags = list(set(existing.tags + new_item.tags))
                    existing.participants = list(set(existing.participants + new_item.participants))
                    logger.debug("Merged similar memory into %s", existing.id)
                    return existing.id
        return None

    @staticmethod
    def _backup_old_file(path: Path) -> None:
        """备份旧文件到 backups/ 目录。"""
        if not path.exists():
            return
        try:
            from bot.safe_io import backup_file
            backup_file(path)
        except Exception as exc:
            logger.warning("Failed to backup %s: %s", path, exc)

    @staticmethod
    def _backup_corrupted(text: str) -> None:
        """将损坏的 JSON 内容写入 backups/ 目录以便排查。"""
        try:
            from pathlib import Path
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = backup_dir / f"corrupted_memory_{ts}.txt"
            path.write_text(text[:10000], encoding="utf-8")
            logger.info("Corrupted memory content saved to %s", path)
        except Exception:
            pass

    @staticmethod
    def _atomic_write_json(path: Path, data: list) -> None:
        """原子写入 JSON（先写 .tmp，flush+fsync，replace）。"""
        from bot.safe_io import atomic_write_json as _awj
        _awj(path, data, backup=True)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _new_id() -> str:
    """生成唯一记忆 ID：mem_ + 8 位随机 hex。"""
    return "mem_" + uuid.uuid4().hex[:8]


def _now() -> str:
    """返回当前 ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat()
