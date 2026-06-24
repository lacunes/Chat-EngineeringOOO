"""关系网络管理器。

管理角色间结构化关系数据（好感/信任/畏惧/依赖/怀疑/敌意）。
每个世界独立 JSON 文件，支持非对称关系，原子写入。
"""

import json
import logging
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone

from config import prompts, settings


logger = logging.getLogger("bot.relation")

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

    def __init__(self, world_name: str, event_bus=None):
        settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.world_name = world_name
        self.file_path = settings.MEMORY_DIR / f"{world_name}_relationships.json"
        self._lock = threading.RLock()
        self._event_bus = event_bus  # 可选：EventBus 实例，用于发射 relationship_changed 事件

        self.characters: list[str] = []
        self.relations: dict[str, dict] = {}       # "角色A->角色B": {...}
        self._reply_count_since_extract: int = 0   # 距上次抽取的 AI 回复数
        self.revision: int = 0
        self.last_modified_source: str = "load"
        self.last_modified_at: str = ""
        self.last_change: dict | None = None

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
            self.revision = max(0, int(data.get("revision", 0)))
            self.last_modified_source = data.get("last_modified_source", "load")
            self.last_modified_at = data.get("last_modified_at", "")
            self.last_change = data.get("last_change")
            logger.info(
                "Loaded relationships for '%s': %d chars, %d relations",
                self.world_name, len(self.characters), len(self.relations),
            )
        except Exception as exc:
            logger.error("Failed to load relationships: %s", exc)

    def save(self) -> None:
        with self._lock:
            self._atomic_write()

    def _atomic_write(self) -> None:
        from bot.safe_io import atomic_write_json
        payload = {
            "characters": self.characters,
            "relations": self.relations,
            "_reply_count_since_extract": self._reply_count_since_extract,
            "revision": self.revision,
            "last_modified_source": self.last_modified_source,
            "last_modified_at": self.last_modified_at,
            "last_change": self.last_change,
        }
        atomic_write_json(self.file_path, payload)
        self._assert_persisted_consistency(payload)

    def _assert_persisted_consistency(self, expected: dict) -> None:
        """写入后立即验证内存、JSON 和下一轮 Prompt 的数据源一致。"""
        if not self.file_path.exists():
            if not expected.get("characters") and not expected.get("relations") and expected.get("revision", 0) == 0:
                return
            raise RuntimeError("Relationship persistence mismatch: JSON file missing")
        persisted = json.loads(self.file_path.read_text(encoding="utf-8"))
        for key in ("characters", "relations", "revision"):
            if persisted.get(key) != expected.get(key):
                raise RuntimeError(f"Relationship persistence mismatch: {key}")

    def get_debug_info(self) -> dict:
        """返回关系页面使用的只读调试信息。"""
        with self._lock:
            consistency_ok = True
            consistency_error = ""
            try:
                expected = {
                    "characters": self.characters,
                    "relations": self.relations,
                    "revision": self.revision,
                }
                self._assert_persisted_consistency(expected)
            except Exception as exc:
                consistency_ok = False
                consistency_error = str(exc)
            return {
                "world": self.world_name,
                "instance_id": f"RelationshipManager@{id(self):x}",
                "revision": self.revision,
                "json_path": str(self.file_path),
                "last_modified_source": self.last_modified_source,
                "last_modified_at": self.last_modified_at or "--",
                "last_change": deepcopy(self.last_change),
                "prompt_summary": self.get_summary(),
                "consistency_ok": consistency_ok,
                "consistency_error": consistency_error,
            }

    def warn_if_reply_exposes_relation_numbers(self, reply: str) -> list[str]:
        """检测明确的关系面板式数字表达；只记录警告，不改写正文。"""
        matches = [match.group(0) for match in _RELATION_NUMBER_PATTERN.finditer(reply)]
        if matches:
            logger.warning(
                "Relation numeric disclosure detected world=%s matches=%s",
                self.world_name, " | ".join(matches[:5]),
            )
        return matches

    def snapshot_state(self) -> dict:
        """为 Web 手动修改建立事务前快照。"""
        with self._lock:
            return {
                "characters": deepcopy(self.characters),
                "relations": deepcopy(self.relations),
                "reply_count": self._reply_count_since_extract,
                "revision": self.revision,
                "last_modified_source": self.last_modified_source,
                "last_modified_at": self.last_modified_at,
                "last_change": deepcopy(self.last_change),
            }

    def commit_web_manual_change(self, before_state: dict, action: str) -> list[dict]:
        """提交已在锁内完成的 Web 修改，并统一更新版本、日志和 JSON。"""
        with self._lock:
            state_changed = (
                before_state["characters"] != self.characters
                or before_state["relations"] != self.relations
                or before_state["reply_count"] != self._reply_count_since_extract
            )
            if not state_changed:
                return []

            records = _build_dimension_change_records(
                before_state["relations"], self.relations, source="web_manual",
            )
            previous_metadata = (
                self.revision,
                self.last_modified_source,
                self.last_modified_at,
                deepcopy(self.last_change),
            )
            self.revision += 1
            self.last_modified_source = "web_manual"
            self.last_modified_at = _utc_now()
            self.last_change = records[-1] if records else {"action": action}
            try:
                self.save()
            except Exception:
                self.characters = deepcopy(before_state["characters"])
                self.relations = deepcopy(before_state["relations"])
                self._reply_count_since_extract = before_state["reply_count"]
                (
                    self.revision,
                    self.last_modified_source,
                    self.last_modified_at,
                    self.last_change,
                ) = previous_metadata
                raise

            if records:
                _log_relation_change_records(
                    self.world_name, records, source="web_manual", operator="web",
                )
            else:
                logger.info(
                    "[RELATION_CHANGE] world=%s source=web_manual action=%s operator=web revision=%d",
                    self.world_name, action, self.revision,
                )
            return records

    # ═══════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════

    def _build_relation_lines(self, prefix: str = "- ", indent: str = "") -> list[str]:
        """构建关系摘要行（去重逻辑）。≥ DEADLOCK_THRESHOLD 或 ≤ LOWER_LOCK 的值显示 🔒。"""
        lines = []
        for key, rel in self.relations.items():
            parts = []
            for dim, label in DIMENSION_LABELS.items():
                val = rel.get(dim, 0)
                if val == 0:
                    continue
                if val >= self.DEADLOCK_THRESHOLD or val <= self.DEADLOCK_LOWER:
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
                lock = "🔒" if (val >= self.DEADLOCK_THRESHOLD or val <= self.DEADLOCK_LOWER) else ""
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
        with self._lock:
            self.characters = []
            self.relations = {}
            self._reply_count_since_extract = 0
            self.revision += 1
            self.last_modified_source = "reset"
            self.last_modified_at = _utc_now()
            self.last_change = {"action": "reset"}
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

    # 维度值 ≥ DEADLOCK_THRESHOLD 时视为"死锁极高"，不再被自动抽取降低
    # 维度值 ≤ DEADLOCK_LOWER 时视为"死锁极低"，不再被自动抽取提高
    DEADLOCK_THRESHOLD: int = 110
    DEADLOCK_LOWER: int = -100

    # 不纳入关系网络的名称（玩家角色、通用称呼等）
    _IGNORED_NAMES: set[str] = {"用户", "玩家", "我", "你", "他", "她", "它"}

    def apply_changes(
        self,
        changes: list[dict],
        message_index: int,
        *,
        expected_revision: int | None = None,
        extraction_metadata: dict | None = None,
    ) -> list[dict] | None:
        """应用抽取出的 delta，返回逐维度变更记录；过时结果返回 None。

        持有 _lock 全程保护，防止与 Web 面板并发保存冲突。
        修改前创建快照，异常时自动回滚。
        变更后通过 EventBus 发射 relationship_changed 事件。
        """
        with self._lock:
            logger.info(
                "[RELATION_APPLY] world=%s base_relation_version=%s "
                "current_relation_version=%d message_range=%s",
                self.world_name,
                expected_revision if expected_revision is not None else "none",
                self.revision,
                (extraction_metadata or {}).get("message_range", "unknown"),
            )
            if expected_revision is not None and expected_revision != self.revision:
                logger.warning(
                    "stale relation extraction discarded world=%s base_relation_version=%d "
                    "current_relation_version=%d message_range=%s",
                    self.world_name, expected_revision, self.revision,
                    (extraction_metadata or {}).get("message_range", "unknown"),
                )
                return None

            snapshot = deepcopy(self.relations)
            characters_snapshot = list(self.characters)
            metadata_snapshot = (
                self.revision,
                self.last_modified_source,
                self.last_modified_at,
                deepcopy(self.last_change),
            )
            records: list[dict] = []

            try:
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

                    for dim in DIMENSION_LABELS:
                        if dim in delta:
                            before_value = rel[dim]
                            if before_value >= self.DEADLOCK_THRESHOLD or before_value <= self.DEADLOCK_LOWER:
                                # 死锁维度：手动设为 110/-100 后不再自动变化
                                continue
                            requested_delta = int(delta[dim])
                            if requested_delta == 0:
                                continue
                            after_value = max(0, min(100, before_value + requested_delta))
                            actual_delta = after_value - before_value
                            if actual_delta == 0:
                                continue
                            rel[dim] = after_value
                            records.append({
                                "pair": f"{from_char}→{to_char}",
                                "dimension": dim,
                                "before_value": before_value,
                                "delta": actual_delta,
                                "after_value": after_value,
                                "reason": note,
                                "trigger_message_index": message_index,
                            })

                    if any(record["pair"] == f"{from_char}→{to_char}" for record in records):
                        rel["last_updated"] = message_index
                        if note and note not in rel["notes"]:
                            rel["notes"].append(note)
                            if len(rel["notes"]) > 10:
                                rel["notes"] = rel["notes"][-10:]

                if records:
                    self.revision += 1
                    self.last_modified_source = "auto_extract"
                    self.last_modified_at = _utc_now()
                    self.last_change = deepcopy(records[-1])
                    self.save()

            except Exception:
                logger.error(
                    "Relation apply_changes failed, rolling back to snapshot (%d keys)",
                    len(snapshot), exc_info=True,
                )
                self.relations = snapshot
                self.characters = characters_snapshot
                (
                    self.revision,
                    self.last_modified_source,
                    self.last_modified_at,
                    self.last_change,
                ) = metadata_snapshot
                return []

            if records:
                _log_relation_change_records(
                    self.world_name, records, source="auto_extract",
                    extraction_metadata=extraction_metadata,
                )
                if self._event_bus:
                    try:
                        self._event_bus.emit(
                            "relationship_changed",
                            world_id=self.world_name,
                            changes=changes,
                            applied_changes=records,
                            revision=self.revision,
                        )
                    except Exception as exc:
                        logger.debug("EventBus emit relationship_changed failed: %s", exc)
        return records

    # ═══════════════════════════════════════════════════════
    # 自动抽取
    # ═══════════════════════════════════════════════════════

    def on_assistant_reply(self) -> None:
        """每次 AI 回复后调用，递增计数。"""
        self._reply_count_since_extract += 1

    def _should_extract(self) -> bool:
        return self._reply_count_since_extract >= settings.RELATION_EXTRACT_INTERVAL

    async def auto_extract(self, memory: list[dict], client) -> None:
        """每 N 轮 AI 回复后自动抽取关系变化。"""
        if not self._should_extract():
            return

        recent = memory[-settings.AUTO_MEMORY_LOOKBACK:]
        trigger_reason = "interval_reached"

        # 本地关键词预检（零 API 开销）
        # 特殊情况：角色数不足 2 时跳过信号检查，允许系统通过 AI 提取来发现角色（引导启动）
        if settings.RELATION_EXTRACT_REQUIRE_SIGNAL:
            if len(self.characters) >= 2:
                if not _should_extract_relations(recent, self.characters):
                    logger.debug(
                        "Relation extract skipped: no signal (%d replies since last, %d chars, %d relations)",
                        self._reply_count_since_extract, len(self.characters), len(self.relations),
                    )
                    self._reply_count_since_extract = 0
                    self.save()
                    return
                trigger_reason = "signal_detected"
            else:
                # 角色数不足，跳过信号检查以引导启动
                trigger_reason = "bootstrap"
                logger.info("Relation extract: bootstrap mode (only %d chars known)", len(self.characters))

        dialogue = _format_dialogue_for_extraction(recent)
        extraction_started_at = _utc_now()
        with self._lock:
            base_relation_version = self.revision
        first_message = recent[0] if recent else {}
        last_message = recent[-1] if recent else {}
        message_range = (
            f"{first_message.get('id', 'index-' + str(max(0, len(memory) - len(recent))))}"
            f"..{last_message.get('id', 'index-' + str(max(0, len(memory) - 1)))}"
        )
        logger.info(
            "[RELATION_EXTRACTION] world=%s extraction_started_at=%s message_range=%s "
            "message_count=%d base_relation_version=%d trigger=%s",
            self.world_name, extraction_started_at, message_range, len(recent),
            base_relation_version, trigger_reason,
        )

        try:
            result, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.RELATION_EXTRACT_PROMPT},
                    {"role": "user", "content": f"最近对话：\n\n{dialogue[:4000]}"},
                ],
                max_tokens=300,
                temperature=0.3,
                purpose="relation_extract",
            )
            changes = _parse_relation_json(result)
            extraction_finished_at = _utc_now()
            extraction_metadata = {
                "extraction_started_at": extraction_started_at,
                "extraction_finished_at": extraction_finished_at,
                "message_range": message_range,
                "base_relation_version": base_relation_version,
            }
            logger.info(
                "[RELATION_EXTRACTION] world=%s extraction_finished_at=%s message_range=%s "
                "base_relation_version=%d current_relation_version=%d parsed_change_groups=%d",
                self.world_name, extraction_finished_at, message_range,
                base_relation_version, self.revision, len(changes),
            )
            if changes:
                msg_idx = len(memory)
                applied_changes = self.apply_changes(
                    changes,
                    msg_idx,
                    expected_revision=base_relation_version,
                    extraction_metadata=extraction_metadata,
                )
                if applied_changes is None:
                    logger.info(
                        "Relation extraction [%s]: stale result discarded",
                        trigger_reason,
                    )
                elif applied_changes:
                    logger.info(
                        "Relation extraction [%s]: %d change groups → %d dimension changes",
                        trigger_reason, len(changes), len(applied_changes),
                    )
                else:
                    logger.info(
                        "Relation extraction [%s]: %d changes parsed but 0 deltas applied (may be deadlocked)",
                        trigger_reason, len(changes),
                    )
            else:
                logger.info(
                    "Relation extraction [%s]: no changes detected (AI returned no_change or [])",
                    trigger_reason,
                )
        except Exception as exc:
            logger.warning(
                "Relation extraction failed [%s]: %s extraction_started_at=%s "
                "extraction_finished_at=%s message_range=%s base_relation_version=%d "
                "current_relation_version=%d",
                trigger_reason, exc, extraction_started_at, _utc_now(), message_range,
                base_relation_version, self.revision,
            )
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


# ── 本地关系信号判断 ──

_RELATION_SIGNAL_KEYWORDS = {
    "信任", "怀疑", "喜欢", "讨厌", "害怕", "依赖", "承诺", "背叛",
    "保护", "生气", "道歉", "亲近", "疏远", "试探", "安慰", "嫉妒",
    "吃醋", "照顾", "冷淡", "和好", "感动", "感激", "失望",
}

_STRONG_SIGNALS = {"背叛", "承诺", "喜欢", "讨厌", "和好", "害怕", "依赖"}


def _should_extract_relations(messages: list[dict], characters: list[str]) -> bool:
    """本地判断最近对话是否包含关系变化信号。零 API 开销。"""
    if not characters or len(characters) < 2:
        return False
    text = " ".join(m.get("content", "") for m in messages[-12:])
    char_hits = sum(1 for c in characters if c in text)
    if char_hits < 2:
        return False
    kw_hits = [kw for kw in _RELATION_SIGNAL_KEYWORDS if kw in text]
    if any(kw in _STRONG_SIGNALS for kw in kw_hits):
        return True
    return len(kw_hits) >= 2


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


def _snapshot_relations(relations: dict) -> dict[str, dict]:
    """对当前关系取快照，仅保存 6 个维度的值。"""
    dims = ["affection", "trust", "fear", "dependence", "suspicion", "hostility"]
    snap: dict[str, dict] = {}
    for key, rel in relations.items():
        snap[key] = {d: rel.get(d, 0) for d in dims}
    return snap


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _display_pair(key: str) -> str:
    return key.replace("->", "→", 1)


def _build_dimension_change_records(
    before_relations: dict,
    after_relations: dict,
    *,
    source: str,
) -> list[dict]:
    """根据前后状态生成明确区分 before/delta/after 的逐维度记录。"""
    records: list[dict] = []
    all_keys = set(before_relations) | set(after_relations)
    for key in sorted(all_keys):
        before = before_relations.get(key, {})
        after = after_relations.get(key, {})
        for dimension in DIMENSION_LABELS:
            before_value = int(before.get(dimension, 0))
            after_value = int(after.get(dimension, 0))
            if before_value == after_value:
                continue
            records.append({
                "pair": _display_pair(key),
                "dimension": dimension,
                "before_value": before_value,
                "delta": after_value - before_value,
                "after_value": after_value,
                "reason": "",
                "source": source,
            })
    return records


def _safe_log_text(value: object) -> str:
    return str(value or "-").replace("\r", " ").replace("\n", " ")[:300]


def _log_relation_change_records(
    world_name: str,
    records: list[dict],
    *,
    source: str,
    operator: str | None = None,
    extraction_metadata: dict | None = None,
) -> None:
    """按一行一维度写入 relation.log，便于审计与检索。"""
    metadata = extraction_metadata or {}
    for record in records:
        delta = int(record["delta"])
        fields = [
            "[RELATION_CHANGE]",
            f"world={_safe_log_text(world_name)}",
            f"source={source}",
            f"pair={_safe_log_text(record['pair'])}",
            f"dimension={record['dimension']}",
            f"before={record['before_value']}",
            f"delta={delta:+d}",
            f"after={record['after_value']}",
        ]
        if source == "auto_extract":
            fields.extend([
                f"reason={_safe_log_text(record.get('reason'))}",
                f"trigger_message_index={record.get('trigger_message_index', '-')}",
                f"message_range={_safe_log_text(metadata.get('message_range'))}",
                f"base_relation_version={metadata.get('base_relation_version', '-')}",
            ])
        if operator:
            fields.append(f"operator={operator}")
        logger.info(" ".join(fields))


_RELATION_NUMBER_PATTERN = re.compile(
    r"(?:当前)?(?:好感|信任|畏惧|依赖|怀疑|敌意)(?:度|值)?"
    r"\s*(?:已经|已)?\s*(?:达到|变为|为|是|增加|减少|上升|下降|[:：=])?"
    r"\s*[+\-]?\d+(?:\.\d+)?"
)
