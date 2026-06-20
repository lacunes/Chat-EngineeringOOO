import json
import logging
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from bot import utils
from bot.memory_store import MemoryStore, MemoryItem, _new_id, _now
from config import prompts, settings


logger = logging.getLogger("bot.memory")

_LONG_MEMORY_SIGNAL_KEYWORDS = {
    "答应", "保证", "约定", "发誓", "记住",
    "现在是", "已经成了", "不再是",
    "经常", "总是", "每天", "从不", "一直",
    "死了", "离开了", "结婚了", "找到了",
    "喜欢", "讨厌", "最怕", "最爱", "从不吃",
}

# ── 记忆分类辅助 ──

_VALID_CATEGORIES = {
    "hard_fact", "relationship", "plot_fact", "user_preference",
    "temporary_state", "character_state", "world_state",
    "legacy",  # 旧记忆兼容标签
}

_CATEGORY_LABEL = re.compile(r"^\[([a-z_]+)\]\s*")


def _parse_category(text: str) -> tuple[str, str]:
    """从记忆文本中解析分类标签，返回 (category, content_without_tag)。

    无标签的旧记忆视为 'legacy'。
    """
    m = _CATEGORY_LABEL.match(text)
    if m:
        cat = m.group(1)
        if cat in _VALID_CATEGORIES:
            return cat, text[m.end():].strip()
    return "legacy", text.strip()


def _count_categories(items: list[str]) -> dict[str, int]:
    """统计各分类的条目数。"""
    counts: dict[str, int] = {}
    for item in items:
        cat, _ = _parse_category(item)
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _should_extract_long_memory(text: str) -> bool:
    """本地判断最近对话是否包含值得长期记忆的信号。"""
    return any(kw in text for kw in _LONG_MEMORY_SIGNAL_KEYWORDS)


class MemoryManager:
    """管理当前世界的短期记忆和长期记忆。

    短期记忆（不变）：
    data/sessions/{world}_chat.json       — 短期聊天上下文

    长期记忆（v3 结构化）：
    data/memory/{world}_memories.json     — 新结构化格式
    data/memory/{world}_long_term.json    — 旧格式（自动迁移）
    data/memory/{world}_summary.json      — 压缩摘要

    v3 起长期记忆使用 MemoryStore 管理，支持类型/参与者/重要性/生命周期。
    旧格式（扁平字符串列表）在首次加载时自动迁移。
    """

    def __init__(self, world_name: str):
        sessions_dir = settings.BASE_DIR / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        self.world_name = world_name

        # 文件路径
        self._chat_path = sessions_dir / f"{world_name}_chat.json"
        self._summary_path = settings.BASE_DIR / "data" / "memory" / f"{world_name}_summary.json"

        # 旧路径（v1 迁移用）
        old_memory_dir = settings.BASE_DIR / "memory"
        self._old_chat_path = old_memory_dir / f"{world_name}_memory.json"

        # ── 结构化长期记忆存储（v3）──
        self._store = MemoryStore(world_name, settings.BASE_DIR)

        # ── 旧路径 → 新路径迁移（短期记忆）──
        self._migrate_if_needed()

        # 短期记忆（不变）
        self.memory: list[dict] = []
        self.summary_log: list[str] = []
        self.last_auto_memory_index = 0
        self.reset_confirm_users: dict[int, float] = {}
        self._lock = threading.Lock()

        # 健康状态追踪
        self._last_save_ok: bool = True
        self._last_save_time: str = ""
        self._was_recovered: bool = False
        self._empty_protection_triggered: bool = False

        # 加载短期记忆和摘要
        self.memory = self._load_json_list(self._chat_path, "short memory")
        self.summary_log = self._load_json_list(self._summary_path, "summary log")
        self.last_auto_memory_index = len(self.memory)

    # ── 长期记忆（向后兼容属性）──

    @property
    def long_memory(self) -> list[str]:
        """以旧格式文本列表的形式访问长期记忆（向后兼容）。"""
        return self._store.to_text_list(limit=settings.LONG_MEMORY_MAX_ITEMS + 20)

    @long_memory.setter
    def long_memory(self, value: list[str]) -> None:
        """从旧格式文本列表设置长期记忆（仅 reset 使用）。"""
        # 这个 setter 仅用于兼容旧代码中的 reset 等操作
        # 正常情况下不应直接赋值
        pass

    @property
    def long_memory_count(self) -> int:
        """活跃长期记忆数量。"""
        return self._store.count

    def _get_real_memories(self) -> list[str]:
        """返回过滤后的真实记忆列表（向后兼容）。"""
        return self._store.to_text_list()

    def delete_long_memory_by_index(self, index: int) -> str | None:
        """按显示列表索引删除长期记忆。返回被删除记忆的内容，或 None。"""
        active = self._store.active_items()
        if 0 <= index < len(active):
            item = active[index]
            self._store.set_status(item.id, "deleted")
            logger.info("Deleted memory #%d: %s", index + 1, item.content[:50])
            return item.content
        return None

    def edit_long_memory_by_index(self, index: int, new_text: str) -> bool:
        """按显示列表索引编辑长期记忆。返回是否成功。"""
        active = self._store.active_items()
        if 0 <= index < len(active):
            item = active[index]
            self._store.update(item.id, content=new_text, updated_at=_now())
            logger.info("Edited memory #%d", index + 1)
            return True
        return False

    # ── 旧路径 → 新路径迁移（仅短期记忆）──

    def _migrate_if_needed(self) -> None:
        """如果旧路径存在短期记忆文件且新路径不存在，自动迁移。"""
        if self._old_chat_path.exists() and not self._chat_path.exists():
            try:
                data = json.loads(self._old_chat_path.read_text(encoding="utf-8"))
                self._chat_path.parent.mkdir(parents=True, exist_ok=True)
                self._chat_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Migrated short memory: %s → %s", self._old_chat_path, self._chat_path)
                self._was_recovered = True
            except Exception as exc:
                logger.warning("Failed to migrate short memory: %s", exc)

    @property
    def message_count(self) -> int:
        return len(self.memory)

    def add_user_message(self, text: str) -> None:
        with self._lock:
            self.memory.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        with self._lock:
            self.memory.append({"role": "assistant", "content": text})

    def last_assistant_message(self) -> str | None:
        """返回最近一条 assistant 消息的文本，没有则返回 None。"""
        for msg in reversed(self.memory):
            if msg.get("role") == "assistant":
                return msg.get("content")
        return None

    def add_long_memory_item(self, text: str) -> None:
        """添加一条长期记忆（自动解析旧分类标签，转为结构化记录）。

        relationship 类型已由 RelationshipManager 独立管理，此处跳过。
        """
        clean = utils.normalize_text(text)
        if not clean:
            return

        # 解析旧分类标签
        from bot.memory_store import OLD_CATEGORY_TO_TYPE, _OLD_CATEGORY_RE
        item_type = "fact"
        content = clean
        m = _OLD_CATEGORY_RE.match(content)
        if m:
            cat = m.group(1)
            item_type = OLD_CATEGORY_TO_TYPE.get(cat, "fact")
            content = content[m.end():].strip()
        else:
            # 无标签默认为 preference
            item_type = "preference"

        # ── relationship 由 RelationshipManager 独立管理，不写入 MemoryStore ──
        if item_type == "relationship":
            logger.debug("Skipped relationship memory (handled by RelationshipManager): %s", content[:80])
            return

        item = MemoryItem(
            id=_new_id(),
            world_id=self.world_name,
            type=item_type,
            content=content,
            importance=0.5,
            confidence=0.8,
            status="active",
            created_at=_now(),
            updated_at=_now(),
        )
        self._store.add(item)

    def reset(self) -> None:
        """重置当前世界的所有记忆（需二次确认才调用）。"""
        with self._lock:
            self.memory = []
            self.summary_log = []
            self.last_auto_memory_index = 0
            self._was_recovered = False
            self._last_save_ok = True
            # 清空 store 中的所有记忆（标记为 deleted）
            for item in self._store.active_items():
                self._store.set_status(item.id, "deleted")
            self.save_memory(force=True)
            self._save_summary_log(force=True)

    def build_messages(
        self,
        world_prompt: str,
        long_term_context: str | None = None,
        dynamic_state: str | None = None,
    ) -> list:
        """构建发送给模型的完整消息列表。

        按「固定 → 半固定 → 动态 → 对话」的顺序排列，
        最大化 DeepSeek prefix cache 命中率。

        [msg 0] world_prompt       — 世界设定 + 时间指令（世界不变则永远不变）
        [msg 1] long_term_context  — 长期记忆（偶尔变）
        [msg 2] dynamic_state      — 当前状态块（每轮变）
        [msg 3..] 最近短期对话      — 最易变
        """
        messages = [{"role": "system", "content": world_prompt}]

        if long_term_context:
            messages.append({
                "role": "system",
                "content": long_term_context,
            })

        if dynamic_state:
            messages.append({
                "role": "system",
                "content": dynamic_state,
            })

        messages.extend(self.memory[-settings.CONTEXT_LENGTH:])
        return messages

    async def compress_old_memory(self, client) -> bool:
        """短期记忆过长时压缩旧对话。返回是否实际执行了压缩。"""
        if len(self.memory) <= settings.MEMORY_MAX_LENGTH:
            return False

        old_size = len(self.memory)
        old_half = self.memory[: old_size // 2]
        dialogue_text = utils.format_dialogue(old_half)

        try:
            summary, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.SUMMARY_PROMPT},
                    {"role": "user", "content": f"请压缩以下对话：\n\n{dialogue_text[:6000]}"},
                ],
                max_tokens=400,
                temperature=0.4,
                purpose="memory_compress",
            )
            logger.info("Compressed old memory through LLM")
        except Exception as exc:
            logger.warning("Memory compression failed, using fallback: %s", exc)
            summary = dialogue_text[:2000]

        if summary:
            self.add_long_memory_item(f"[event] 旧剧情摘要：{summary}")
            # 追加摘要日志
            with self._lock:
                self.summary_log.append(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {summary[:200]}"
                )
                if len(self.summary_log) > 20:
                    self.summary_log = self.summary_log[-20:]
            await self.refine_long_memory(client, force=False)

        with self._lock:
            self.memory = self.memory[old_size // 2 :]
            self.last_auto_memory_index = len(self.memory)
        self.save_memory()
        self._save_summary_log()
        logger.info("Short memory compressed: %s -> %s", old_size, len(self.memory))
        return True

    async def auto_extract_long_memory(self, client) -> None:
        # 每隔固定消息数，从最近对话中抽取稳定事实。
        new_messages = len(self.memory) - self.last_auto_memory_index
        if new_messages < settings.AUTO_MEMORY_INTERVAL:
            return

        recent = self.memory[-settings.AUTO_MEMORY_LOOKBACK:]
        if settings.LONG_MEMORY_EXTRACT_REQUIRE_SIGNAL:
            dialogue_text = " ".join(m.get("content", "") for m in recent)
            if not _should_extract_long_memory(dialogue_text):
                logger.debug("Long memory extract skipped: no signal")
                with self._lock:
                    self.last_auto_memory_index = len(self.memory)
                return
        dialogue = utils.format_dialogue(recent, limit=7000)

        try:
            extracted_text, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.LONG_MEMORY_EXTRACT_PROMPT},
                    {"role": "user", "content": f"最近对话：\n\n{dialogue}"},
                ],
                max_tokens=500,
                temperature=0.35,
                purpose="memory_extract",
            )
            raw_items = utils.parse_memory_json(extracted_text)
            new_items = utils.parse_memory_items(extracted_text) if raw_items else []

            # ── 过滤：relationship 类型由 RelationshipManager 独立管理 ──
            rel_filtered = [it for it in new_items if not it.startswith("[relationship]")]
            if len(rel_filtered) != len(new_items):
                logger.debug(
                    "Filtered %d relationship items from memory extraction (handled by RelationshipManager)",
                    len(new_items) - len(rel_filtered),
                )
            new_items = rel_filtered

            # 记录清洗掉了多少垃圾
            if raw_items and len(raw_items) != len(new_items):
                removed_count = len(raw_items) - len(new_items)
                logger.info(
                    "Memory cleanup: removed %d garbage items from %d raw items",
                    removed_count, len(raw_items),
                )

            if new_items:
                # 统计分类
                cat_counts = _count_categories(new_items)
                temp_items = [it for it in new_items if it.startswith("[temporary_state]")]
                logger.info(
                    "Auto extracted %d long memory items: %s%s",
                    len(new_items),
                    ", ".join(f"{c}={n}" for c, n in sorted(cat_counts.items())),
                    f" (temporary_state={len(temp_items)})" if temp_items else "",
                )
            else:
                logger.info("Auto memory extract ran — no new items (all filtered or no signal)")

            for item in new_items:
                self.add_long_memory_item(item)
            if new_items:
                self.save_long_memory()
                logger.info("Auto extracted %s long memory items", len(new_items))
            with self._lock:
                self.last_auto_memory_index = len(self.memory)
            await self.refine_long_memory(client, force=False)
        except Exception as exc:
            logger.warning("Auto long memory extraction failed: %s", exc)
            with self._lock:
                self.last_auto_memory_index = len(self.memory)

    async def refine_long_memory(self, client, force: bool = False) -> None:
        """长期记忆精炼：AI 去重合并。已迁移到 MemoryStore 内置去重。"""
        # v3: MemoryStore 已内置内容去重，精炼改为调用 store 的合并逻辑
        active = self._store.active_items()
        if not force and len(active) <= settings.LONG_MEMORY_MAX_ITEMS + settings.LONG_MEMORY_REFINE_BUFFER:
            return

        # 使用 AI 精炼
        text_items = [f"[{m.type}] {m.content}" for m in active]
        try:
            refined_text, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.LONG_MEMORY_REFINE_PROMPT},
                    {"role": "user", "content": json.dumps(text_items, ensure_ascii=False)},
                ],
                max_tokens=900,
                purpose="memory_refine",
                temperature=0.35,
            )
            refined = utils.parse_memory_items(refined_text)
            if refined:
                # 将精炼结果写回 store（保留前 N 条 active，其余归档）
                keep_ids = {m.id for m in active[:settings.LONG_MEMORY_MAX_ITEMS]}
                for item in active:
                    if item.id not in keep_ids:
                        self._store.set_status(item.id, "archived")
                # 添加精炼后的新条目
                for text in refined:
                    self.add_long_memory_item(text)
                logger.info("Long memory refined: %d items active", self._store.count)
            else:
                logger.warning("Long memory refine returned empty output — memory preserved")
        except Exception as exc:
            logger.warning("Long memory refine failed, skipping: %s", exc)

    def save_memory(self, force: bool = False) -> None:
        """保存短期记忆（force=True 允许覆盖为空）。"""
        self._save("memory", self._chat_path, self.memory, "short memory", force)

    def cleanup_polluted_memories(self) -> dict:
        """清理长期记忆中的垃圾条目。v3: 归档所有旧类别记忆并过滤无效条目。"""
        active = self._store.active_items()
        old_count = len(active)
        removed = 0
        kept = 0

        for item in active:
            # 过滤过短无意义的条目
            if len(item.content.strip()) < 6:
                self._store.set_status(item.id, "archived")
                removed += 1
                continue
            # 过滤纯符号/垃圾行
            has_content = bool(re.search(r'[a-zA-Z一-鿿぀-ゟ゠-ヿ]', item.content))
            if not has_content:
                self._store.set_status(item.id, "archived")
                removed += 1
                continue
            kept += 1

        logger.info(
            "Memory cleanup: removed %d garbage items, kept %d real memories (from %d total)",
            removed, kept, old_count,
        )
        return {
            "before": old_count,
            "after": kept,
            "removed": removed,
            "kept": kept,
        }

    def save_long_memory(self, force: bool = False) -> None:
        """保存长期记忆（v3：委托给 MemoryStore）。"""
        self._store.save()
        self._last_save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_save_ok = True

    def _save_summary_log(self, force: bool = False) -> None:
        """保存压缩摘要日志。"""
        self._save("summary", self._summary_path, self.summary_log, "summary log", force)

    def _save(self, slot: str, path: Path, data, label: str, force: bool = False) -> None:
        """统一持久化入口，带空数据保护。"""
        result = self._atomic_write(path, data, label, force)
        if result is False:
            self._empty_protection_triggered = True
            self._last_save_ok = False
        else:
            self._last_save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._last_save_ok = True

    @staticmethod
    def _load_json_list(path: Path, label: str) -> list:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                # 仅对长期记忆做分类统计（其元素为字符串），短期记忆为 dict 列表
                if any(isinstance(item, str) for item in data[:1]):
                    cat_counts = _count_categories(data)
                    logger.info(
                        "Loaded %s from %s (%d items: %s)",
                        label, path, len(data),
                        ", ".join(f"{c}={n}" for c, n in sorted(cat_counts.items())),
                    )
                else:
                    logger.info(
                        "Loaded %s from %s (%d items)",
                        label, path, len(data),
                    )
                return data
            logger.warning("%s is not a list, using empty list", path)
        except Exception as exc:
            logger.error("Failed to load %s from %s: %s", label, path, exc)
        return []

    @staticmethod
    def _atomic_write(path: Path, data, label: str, force: bool = False) -> bool | None:
        """原子写入 + 自动备份 + 空数据保护。

        1. 如果 data 为空 list 且旧文件非空且 force=False → 拒绝覆盖，记录警告
        2. 备份旧文件到 backups/memory_时间戳.json
        3. 先写 .tmp，再 os.replace（原子操作）
        
        Returns:
            True: 成功写入
            False: 因空数据保护拒绝写入
            None: 异常（写入失败）
        """
        # ── 空数据保护 ──
        if isinstance(data, list) and len(data) == 0 and not force:
            if path.exists():
                try:
                    old_size = path.stat().st_size
                    if old_size > 10:  # 旧文件有实际内容（非空数组 "[]"）
                        logger.critical(
                            "REFUSED to overwrite non-empty %s (%d bytes) with empty data! "
                            "Use force=True only for explicit user reset.",
                            path, old_size,
                        )
                        return False
                except Exception:
                    pass

        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # ── 备份旧文件 ──
            MemoryManager._backup_file(path)

            with tempfile.NamedTemporaryFile(
                mode="w", dir=str(path.parent), prefix=".tmp_", suffix=".json",
                delete=False, encoding="utf-8",
            ) as file:
                tmp = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
            return True
        except Exception as exc:
            logger.error("Failed to save %s: %s", label, exc)
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    @staticmethod
    def _backup_file(path: Path) -> None:
        """备份文件到 backups/ 目录。"""
        if not path.exists():
            return
        try:
            backup_dir = path.parent.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"memory_{path.stem}_{ts}.json"
            shutil.copy2(path, backup_dir / backup_name)
            # 只保留最近 20 个备份
            existing = sorted(backup_dir.glob(f"memory_{path.stem}_*.json"))
            for old_backup in existing[:-20]:
                try:
                    old_backup.unlink()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Failed to backup %s: %s", path, exc)

    # ── 记忆状态查询（供 Web 面板使用）──

    def get_memory_status(self) -> dict:
        """返回记忆健康状况摘要。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def file_info(p: Path) -> dict:
            if not p.exists():
                return {"ok": False, "count": 0, "size": 0, "mtime": "-", "path": str(p)}
            try:
                st = p.stat()
                data = json.loads(p.read_text(encoding="utf-8"))
                count = len(data) if isinstance(data, list) else 0
                return {
                    "ok": True,
                    "count": count,
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "path": str(p),
                }
            except Exception:
                return {"ok": False, "count": 0, "size": 0, "mtime": "?", "path": str(p), "corrupted": True}

        # v3 长期记忆从 store 获取
        long_path = self._store._new_path
        long_info = file_info(long_path)
        long_info["count"] = self._store.count  # 用 store 的活跃计数

        return {
            "world": self.world_name,
            "checked_at": now,
            "last_save_ok": self._last_save_ok,
            "last_save_time": self._last_save_time or "(尚未保存)",
            "was_recovered": self._was_recovered,
            "chat": file_info(self._chat_path),
            "long": long_info,
            "summary": file_info(self._summary_path),
        }
