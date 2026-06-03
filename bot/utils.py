import importlib
import json
import random
from types import ModuleType

from config import settings


def load_world(name: str) -> ModuleType:
    # 动态加载世界文件：ACTIVE_WORLD=one -> import worlds.one。
    # 这样新增世界时不需要改 main.py。
    safe_name = name.strip().lower()
    if not safe_name.isidentifier():
        raise ValueError(f"Invalid ACTIVE_WORLD: {name}")
    return importlib.import_module(f"worlds.{safe_name}")


def format_dialogue(messages: list, limit: int = 6000) -> str:
    text = "\n".join(
        f"[{msg.get('role', '?')}]: {msg.get('content', '')}"
        for msg in messages
    )
    return text[:limit]


def normalize_text(text: str) -> str:
    """规范化单条文本：去首尾空白，折叠内部连续空白为单个空格。"""
    return " ".join(text.strip().split())


def normalize_memory_items(items: list) -> list:
    """去重、去空白、去非字符串，返回干净的字符串列表。"""
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        text = normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def parse_memory_json(text: str) -> list:
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return normalize_memory_items(data)
    except Exception:
        pass

    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789.、 ")
        if line:
            lines.append(line.strip('"“”'))
    return normalize_memory_items(lines)


def split_reply(text: str, threshold: int = None) -> list:
    # Telegram 可以发送长消息，但分段后的阅读体验更好。
    # 这里优先按自然断点切开，找不到标点时再硬切。
    limit = threshold or settings.SPLIT_THRESHOLD
    text = text.strip()
    if len(text) <= limit:
        return [text]

    parts = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        marks = [
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind("。"),
            window.rfind("！"),
            window.rfind("？"),
            window.rfind("，"),
        ]
        pos = max(marks)
        if pos <= 0:
            pos = limit
        part = remaining[:pos + 1].strip()
        if part:
            parts.append(part)
        remaining = remaining[pos + 1:].strip()

    if remaining:
        parts.append(remaining)
    return parts


def get_reply_length() -> int:
    # 85% 走常规长度，15% 走更长回复。
    # 二次方分布会降低超长回复出现概率。
    if random.random() < 0.85:
        return random.randint(settings.MIN_REPLY_TOKENS, settings.MID_REPLY_TOKENS)
    return int(
        settings.MID_REPLY_TOKENS
        + (random.random() ** 2)
        * (settings.MAX_REPLY_TOKENS - settings.MID_REPLY_TOKENS)
    )
