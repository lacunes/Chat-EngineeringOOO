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

    文件路径（v2）：
    data/sessions/{world}_chat.json       — 短期聊天上下文
    data/memory/{world}_long_term.json    — 长期记忆
    data/memory/{world}_summary.json      — 压缩摘要（从短期压缩时生成）

    旧路径（v1，启动时自动迁移）：
    memory/{world}_memory.json
    memory/{world}_world_memory.json
    """

    def __init__(self, world_name: str):
        # 新路径
        sessions_dir = settings.BASE_DIR / "data" / "sessions"
        memory_dir = settings.BASE_DIR / "data" / "memory"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        memory_dir.mkdir(parents=True, exist_ok=True)

        self.world_name = world_name

        # 文件路径（v2）
        self._chat_path = sessions_dir / f"{world_name}_chat.json"
        self._long_term_path = memory_dir / f"{world_name}_long_term.json"
        self._summary_path = memory_dir / f"{world_name}_summary.json"

        # 旧路径（v1 迁移用）
        old_memory_dir = settings.BASE_DIR / "memory"
        self._old_chat_path = old_memory_dir / f"{world_name}_memory.json"
        self._old_long_path = old_memory_dir / f"{world_name}_world_memory.json"

        # ── 启动时自动迁移 ──
        self._migrate_if_needed()

        self.memory: list[dict] = []
        self.long_memory: list[str] = []
        self.summary_log: list[str] = []  # 压缩摘要历史
        self.last_auto_memory_index = 0
        self.reset_confirm_users: dict[int, float] = {}
        self._lock = threading.Lock()

        # 健康状态追踪
        self._last_save_ok: bool = True
        self._last_save_time: str = ""
        self._was_recovered: bool = False

        # 加载数据
        self.memory = self._load_json_list(self._chat_path, "short memory")
        self.long_memory = self._load_json_list(self._long_term_path, "long memory")
        self.summary_log = self._load_json_list(self._summary_path, "summary log")
        self.last_auto_memory_index = len(self.memory)

    # ── 旧路径 → 新路径迁移 ──

    def _migrate_if_needed(self) -> None:
        """如果旧路径存在文件且新路径不存在，自动迁移。"""
        for old_path, new_path, label in [
            (self._old_chat_path, self._chat_path, "short memory"),
            (self._old_long_path, self._long_term_path, "long memory"),
        ]:
            if old_path.exists() and not new_path.exists():
                try:
                    data = json.loads(old_path.read_text(encoding="utf-8"))
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.info("Migrated %s: %s → %s (%d items)", label, old_path, new_path,
                               len(data) if isinstance(data, list) else 0)
                    self._was_recovered = True
                except Exception as exc:
                    logger.warning("Failed to migrate %s: %s", label, exc)

    @property
    def message_count(self) -> int:
        return len(self.memory)

    @property
    def long_memory_count(self) -> int:
        return len(self.long_memory)

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
        clean = utils.normalize_text(text)
        if clean:
            # 如果用户手动添加的记忆没有分类标签，默认为 user_preference
            if not _CATEGORY_LABEL.match(clean):
                clean = f"[user_preference] {clean}"
            with self._lock:
                if clean not in self.long_memory:
                    self.long_memory.append(clean)

    def reset(self) -> None:
        """重置当前世界的所有记忆（需二次确认才调用）。force=True 允许写入空数组。"""
        with self._lock:
            self.memory = []
            self.long_memory = []
            self.summary_log = []
            self.last_auto_memory_index = 0
            self._was_recovered = False
            self._last_save_ok = True
            self.save_memory(force=True)
            self.save_long_memory(force=True)
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
            logger.info("Compressed old memory through DeepSeek")
        except Exception as exc:
            logger.warning("Memory compression failed, using fallback: %s", exc)
            summary = dialogue_text[:2000]

        if summary:
            self.add_long_memory_item(f"[plot_fact] 旧剧情摘要：{summary}")
            # 追加摘要日志
            with self._lock:
                self.summary_log.append(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {summary[:200]}"
                )
                # 只保留最近 20 条摘要
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
            new_items = utils.parse_memory_json(extracted_text)
            if new_items:
                # 统计分类
                cat_counts = _count_categories(new_items)
                # 列出临时状态条目
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
        # 长期记忆先本地去重；数量太多时再请求模型合并精炼。
        with self._lock:
            self.long_memory = utils.normalize_memory_items(self.long_memory)
            items = list(self.long_memory)
        if (
            not force
            and len(items)
            <= settings.LONG_MEMORY_MAX_ITEMS + settings.LONG_MEMORY_REFINE_BUFFER
        ):
            return

        try:
            refined_text, _ = await client.chat(
                [
                    {"role": "system", "content": prompts.LONG_MEMORY_REFINE_PROMPT},
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
                ],
                max_tokens=900,
                purpose="memory_refine",
                temperature=0.35,  # 精炼需要准确合并去重，温度低减少偏差
            )
            refined = utils.parse_memory_json(refined_text)
            with self._lock:
                if refined:
                    self.long_memory = refined[: settings.LONG_MEMORY_MAX_ITEMS]
                    cat_counts = _count_categories(self.long_memory)
                    logger.info(
                        "Long memory refined to %d items: %s",
                        len(self.long_memory),
                        ", ".join(f"{c}={n}" for c, n in sorted(cat_counts.items())),
                    )
                else:
                    logger.warning("Long memory refine returned empty output")
        except Exception as exc:
            logger.warning("Long memory refine failed, using local trim: %s", exc)
            with self._lock:
                self.long_memory = self.long_memory[-settings.LONG_MEMORY_MAX_ITEMS :]

        self.save_long_memory()

    def save_memory(self, force: bool = False) -> None:
        """保存短期记忆（force=True 允许覆盖为空）。"""
        self._save("memory", self._chat_path, self.memory, "short memory", force)

    def save_long_memory(self, force: bool = False) -> None:
        """保存长期记忆（force=True 允许覆盖为空）。"""
        self._save("long_memory", self._long_term_path, self.long_memory, "long memory", force)

    def _save_summary_log(self, force: bool = False) -> None:
        """保存压缩摘要日志。"""
        self._save("summary", self._summary_path, self.summary_log, "summary log", force)

    def _save(self, slot: str, path: Path, data, label: str, force: bool = False) -> None:
        """统一持久化入口，带空数据保护。"""
        self._atomic_write(path, data, label, force)
        self._last_save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    def _atomic_write(path: Path, data, label: str, force: bool = False) -> None:
        """原子写入 + 自动备份 + 空数据保护。

        1. 如果 data 为空 list 且旧文件非空且 force=False → 拒绝覆盖，记录警告
        2. 备份旧文件到 backups/memory_时间戳.json
        3. 先写 .tmp，再 os.replace（原子操作）
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
                        return
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
            os.replace(tmp, path)
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
                return {"exists": False, "size": 0, "mtime": "-", "items": 0}
            try:
                st = p.stat()
                data = json.loads(p.read_text(encoding="utf-8"))
                items = len(data) if isinstance(data, list) else 0
                return {
                    "exists": True,
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "items": items,
                }
            except Exception:
                return {"exists": True, "size": 0, "mtime": "?", "items": 0, "corrupted": True}

        return {
            "world": self.world_name,
            "checked_at": now,
            "last_save_ok": self._last_save_ok,
            "last_save_time": self._last_save_time or "(尚未保存)",
            "was_recovered": self._was_recovered,
            "chat": file_info(self._chat_path),
            "long_term": file_info(self._long_term_path),
            "summary": file_info(self._summary_path),
        }
