"""关系网络管理器。

管理角色间结构化关系数据（好感/信任/畏惧/依赖/怀疑/敌意）。
每个世界独立 JSON 文件，支持非对称关系，原子写入。
"""

import json
import logging
import os
import re
import tempfile

from config import prompts, settings


logger = logging.getLogger(__name__)

DIMENSION_LABELS: dict[str, str] = {
    "affection":   "好感",
    "trust":       "信任",
    "fear":        "畏惧",
    "dependence":  "依赖",
    "suspicion":   "怀疑",
    "hostility":   "敌意",
}


class RelationshipManager:
    """管理当前世界的关系网络。"""

    def __init__(self, world_name: str):
        settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.world_name = world_name
        self.file_path = settings.MEMORY_DIR / f"{world_name}_relationships.json"

        self.characters: list[str] = []
        self.relations: dict[str, dict] = {}       # "角色A->角色B": {...}
        self._reply_count_since_extract: int = 0   # 距上次抽取的 AI 回复数
        self._pending_hints: list[str] = []         # 待追加到下一条回复的变化提示

        self._load()

    # ═══════════════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════════════

    def _load(self) -> None:
        if not self.file_path.exists():
            logger.info("No relationship file for '%s', starting fresh", self.world_name)
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            self.characters = data.get("characters", [])
            self.relations = data.get("relations", {})
            self._reply_count_since_extract = data.get("_reply_count_since_extract", 0)
            logger.info(
                "Loaded relationships for '%s': %d chars, %d relations",
                self.world_name, len(self.characters), len(self.relations),
            )
        except Exception as exc:
            logger.error("Failed to load relationships: %s", exc)

    def save(self) -> None:
        self._atomic_write()

    def _atomic_write(self) -> None:
        tmp = None
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=str(self.file_path.parent),
                prefix=".tmp_rel_", suffix=".json",
                delete=False, encoding="utf-8",
            ) as f:
                tmp = f.name
                json.dump({
                    "characters": self.characters,
                    "relations": self.relations,
                    "_reply_count_since_extract": self._reply_count_since_extract,
                }, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.file_path)
        except Exception as exc:
            logger.error("Failed to save relationships: %s", exc)
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    # ═══════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════

    def _build_relation_lines(self, prefix: str = "- ", indent: str = "") -> list[str]:
        """构建关系摘要行（去重逻辑）。≥ DEADLOCK_THRESHOLD 的值显示 🔒。"""
        lines = []
        for key, rel in self.relations.items():
            parts = []
            for dim, label in DIMENSION_LABELS.items():
                val = rel.get(dim, 0)
                if val == 0:
                    continue
                if val >= self.DEADLOCK_THRESHOLD:
                    parts.append(f"🔒{label}{val}")
                else:
                    parts.append(f"{label}{val}")
            if parts:
                lines.append(f"{indent}{prefix}{key}：{', '.join(parts)}")
        return lines

    def get_summary(self, max_chars: int = 500) -> str:
        """生成注入系统提示词的简短关系摘要。"""
        if not self.relations:
            return ""
        lines = self._build_relation_lines()
        if not lines:
            return ""
        text = "\n".join(["\n[当前角色关系]"] + lines)
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    def get_status_text(self) -> str:
        """简短摘要，供 /relations 命令使用。"""
        if not self.relations:
            return "当前世界暂无角色关系数据。"
        lines = self._build_relation_lines(prefix="", indent="  ")
        return "\n".join([f"🌐 {self.world_name} 关系网络："] + lines)

    def get_full_text(self) -> str:
        """完整关系文本，供 /relation_full 使用。"""
        if not self.relations:
            return "当前世界暂无角色关系数据。"

        lines = [f"世界：{self.world_name}"]
        lines.append(f"角色：{', '.join(self.characters) if self.characters else '（未识别）'}")
        lines.append("")

        for key, rel in self.relations.items():
            parts = []
            for dim, label in DIMENSION_LABELS.items():
                val = rel.get(dim, 0)
                lock = "🔒" if val >= self.DEADLOCK_THRESHOLD else ""
                parts.append(f"  {lock}{label}: {val}")
            notes = rel.get("notes", [])
            note_text = f"\n  备注: {'; '.join(notes[-5:])}" if notes else ""
            last = rel.get("last_updated", 0)
            lines.append(f"{key}（第{last}轮）：")
            lines.extend(parts)
            if note_text:
                lines.append(note_text)
            lines.append("")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════
    # 修改
    # ═══════════════════════════════════════════════════════

    def _get_relation(self, from_char: str, to_char: str) -> dict:
        key = f"{from_char}->{to_char}"
        if key not in self.relations:
            self.relations[key] = self._empty_relation()
        return self.relations[key]

    @staticmethod
    def _empty_relation() -> dict:
        return {
            "affection": 0, "trust": 0, "fear": 0,
            "dependence": 0, "suspicion": 0, "hostility": 0,
            "notes": [], "last_updated": 0,
        }

    def ensure_character(self, name: str) -> None:
        if name not in self.characters:
            self.characters.append(name)
            logger.info("New character in relationship network: %s", name)

    def reset(self) -> None:
        """清空当前世界的关系网络。"""
        self.characters = []
        self.relations = {}
        self._reply_count_since_extract = 0
        self._pending_hints = []
        self.save()
        logger.info("Relationship network reset for world '%s'", self.world_name)

    def _resolve_name(self, name: str) -> str:
        """尝试将昵称变体匹配到已有角色名。

        如 "小B"、"B哥" 中有 "B" 且已有角色 "B"，则返回 "B"。
        只在已有角色≥1个时启用匹配，首次运行时不生效。
        """
        if not self.characters:
            return name
        if name in self.characters:
            return name
        # 检查新名字是否包含已有角色名，或已有角色名包含新名字
        for existing in self.characters:
            if existing in name or name in existing:
                logger.info("Resolved name '%s' → '%s'", name, existing)
                return existing
        return name

    # 维度值 ≥ DEADLOCK_THRESHOLD 时视为"死锁"，不再被自动抽取改变
    DEADLOCK_THRESHOLD: int = 110

    # 不纳入关系网络的名称（玩家角色、通用称呼等）
    _IGNORED_NAMES: set[str] = {"用户", "玩家", "我", "你", "他", "她", "它"}

    def apply_changes(self, changes: list[dict], message_index: int) -> list[str]:
        """应用抽取出的关系变化，返回需显示的提示文本列表。"""
        hints: list[str] = []
        for change in changes:
            from_char = (change.get("from") or "").strip()
            to_char = (change.get("to") or "").strip()
            if not from_char or not to_char or from_char == to_char:
                continue
            # 过滤玩家角色和通用称呼
            if from_char in self._IGNORED_NAMES or to_char in self._IGNORED_NAMES:
                logger.debug("Skipping relation involving ignored name: %s -> %s", from_char, to_char)
                continue

            # 尝试匹配已有角色名（处理昵称变体：如"小B"匹配到"B"）
            from_char = self._resolve_name(from_char)
            to_char = self._resolve_name(to_char)

            self.ensure_character(from_char)
            self.ensure_character(to_char)

            rel = self._get_relation(from_char, to_char)
            delta = change.get("changes", {})
            note = change.get("note", "")

            significant = False
            change_parts: list[str] = []
            for dim in DIMENSION_LABELS:
                if dim in delta:
                    old = rel[dim]
                    if old >= self.DEADLOCK_THRESHOLD:
                        # 死锁维度：手动设为 110 后不再自动变化
                        continue
                    d = int(delta[dim])
                    if d == 0:
                        continue
                    new = max(0, min(100, old + d))
                    actual_d = new - old
                    if actual_d == 0:
                        continue
                    rel[dim] = new
                    sign = "+" if actual_d > 0 else ""
                    change_parts.append(f"{DIMENSION_LABELS[dim]}{sign}{actual_d}")
                    if abs(actual_d) > settings.RELATION_SIGNIFICANT_THRESHOLD:
                        significant = True

            if change_parts:
                rel["last_updated"] = message_index
                if note and note not in rel["notes"]:
                    rel["notes"].append(note)
                    if len(rel["notes"]) > 10:
                        rel["notes"] = rel["notes"][-10:]

                hint = f"{from_char}→{to_char}：{', '.join(change_parts)}"
                if significant:
                    hint += " ⚡"
                hints.append(hint)

        if changes:
            self.save()
        return hints

    # ═══════════════════════════════════════════════════════
    # 自动抽取
    # ═══════════════════════════════════════════════════════

    def on_assistant_reply(self) -> None:
        """每次 AI 回复后调用，递增计数。"""
        self._reply_count_since_extract += 1

    def _should_extract(self) -> bool:
        return self._reply_count_since_extract >= settings.RELATION_EXTRACT_INTERVAL

    def take_pending_hints(self) -> list[str]:
        """取出待显示的变化提示并清空缓存。"""
        hints = self._pending_hints
        self._pending_hints = []
        return hints

    async def auto_extract(self, memory: list[dict], client) -> None:
        """每 N 轮 AI 回复后自动抽取关系变化。"""
        if not self._should_extract():
            return

        recent = memory[-settings.AUTO_MEMORY_LOOKBACK:]
        dialogue = _format_dialogue_for_extraction(recent)

        try:
            result, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.RELATION_EXTRACT_PROMPT},
                    {"role": "user", "content": f"最近对话：\n\n{dialogue[:4000]}"},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            changes = _parse_relation_json(result)
            if changes:
                msg_idx = len(memory)
                hints = self.apply_changes(changes, msg_idx)
                if hints:
                    self._pending_hints.extend(hints)
                    logger.info("Relation extraction: %d changes → %d hints", len(changes), len(hints))
        except Exception as exc:
            logger.warning("Relation extraction failed: %s", exc)
        finally:
            self._reply_count_since_extract = 0
            self.save()


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _format_dialogue_for_extraction(messages: list) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if len(content) > 500:
            content = content[:500] + "…"
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _parse_relation_json(text: str) -> list:
    """容错解析模型返回的 JSON 数组。"""
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse relation JSON: %s", text[:200])
    return []
