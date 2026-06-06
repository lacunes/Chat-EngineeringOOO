import json
import logging
import os
import re
import tempfile
import threading
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

    每个世界使用独立文件：
    memory/one_memory.json
    memory/one_world_memory.json
    """

    def __init__(self, world_name: str):
        settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.world_name = world_name
        self.memory: list[dict] = []
        self.long_memory: list[str] = []
        self.last_auto_memory_index = 0
        self.reset_confirm_users: dict[int, float] = {}
        self._lock = threading.Lock()  # Web 面板与 Bot 并发写入保护

        # 统一加载短期+长期记忆
        for slot, suffix in [("memory", "_memory.json"), ("long_memory", "_world_memory.json")]:
            path = settings.MEMORY_DIR / f"{world_name}{suffix}"
            label = "short memory" if slot == "memory" else "long memory"
            setattr(self, slot, self._load_json_list(path, label))

        self.last_auto_memory_index = len(self.memory)

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
        with self._lock:
            self.memory = []
            self.long_memory = []
            self.last_auto_memory_index = 0
            self.save_memory()
            self.save_long_memory()

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
            await self.refine_long_memory(client, force=False)

        with self._lock:
            self.memory = self.memory[old_size // 2 :]
            self.last_auto_memory_index = len(self.memory)
        self.save_memory()
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

    def save_memory(self) -> None:
        self._save("memory")

    def save_long_memory(self) -> None:
        self._save("long_memory")

    def _save(self, slot: str) -> None:
        """统一持久化入口。slot: 'memory' | 'long_memory'"""
        suffix = "_memory.json" if slot == "memory" else "_world_memory.json"
        label = "short memory" if slot == "memory" else "long memory"
        path = settings.MEMORY_DIR / f"{self.world_name}{suffix}"
        self._atomic_write(path, getattr(self, slot), label)

    @staticmethod
    def _load_json_list(path: Path, label: str) -> list:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                cat_counts = _count_categories(data)
                logger.info(
                    "Loaded %s from %s (%d items: %s)",
                    label, path, len(data),
                    ", ".join(f"{c}={n}" for c, n in sorted(cat_counts.items())),
                )
                return data
            logger.warning("%s is not a list, using empty list", path)
        except Exception as exc:
            logger.error("Failed to load %s from %s: %s", label, path, exc)
        return []

    @staticmethod
    def _atomic_write(path: Path, data, label: str) -> None:
        # 原子写入：先写临时文件，再替换正式文件，降低断电/崩溃时 JSON 写坏的概率。
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
