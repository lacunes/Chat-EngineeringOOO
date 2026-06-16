import json
import random
from types import SimpleNamespace

import yaml

from config import settings


def load_world(name: str):
    """加载世界数据。

    优先级：data/worlds/<name>.yaml → data/worlds/<name>.json → 报错。
    不再支持 worlds/<name>.py 旧格式。
    """
    safe_name = name.strip().lower()
    if not safe_name.isidentifier():
        raise ValueError(f"Invalid ACTIVE_WORLD: {name}")

    data_dir = settings.BASE_DIR / "data" / "worlds"

    # 1. YAML 优先
    yaml_path = data_dir / f"{safe_name}.yaml"
    if yaml_path.exists():
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            return SimpleNamespace(**data)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load world YAML %s, trying JSON: %s", yaml_path, exc
            )

    # 2. JSON 兜底（过渡期兼容）
    json_path = data_dir / f"{safe_name}.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return SimpleNamespace(**data)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load world JSON %s: %s", json_path, exc
            )

    raise FileNotFoundError(
        f"世界 '{safe_name}' 不存在（找不到 {yaml_path} 或 {json_path}）"
    )


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
    """旧版解析器（保留兼容，新代码请用 parse_memory_items）。"""
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


# ── 垃圾行模式（长期记忆污染清洗用）──

_GARBAGE_PATTERNS = frozenset({
    "```json", "```", "[", "]", "{", "}", "},", ",",
})


def parse_memory_items(raw: str) -> list[str]:
    """清洗模型返回的长期记忆提取/精炼结果，返回干净的字符串列表。

    处理规则：
    1. 去除 ```json ... ``` 代码块包裹
    2. 尝试 json.loads 解析
    3. 如果是 list，只保留字符串元素
    4. 解析失败按行兜底
    5. 兜底按行过滤垃圾行
    6. 去重
    7. 只保留包含分类标签或有明确正文的句子
    """
    import re

    _VALID_MEMORY_TAG = re.compile(
        r"\[(?:hard_fact|relationship|plot_fact|character_state"
        r"|user_preference|temporary_state|world_state|legacy"
        r"|fact|event|promise|preference|secret|goal|scene_state)\]"
    )

    text = raw.strip()

    # 1. 去除代码块包裹
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    # 2. 尝试 JSON 解析
    items = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    cleaned = normalize_text(item)
                    if cleaned:
                        items.append(cleaned)
            if items:
                return _filter_valid_memories(items)
        elif isinstance(data, dict) and "items" in data:
            nested = data.get("items", [])
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, str):
                        cleaned = normalize_text(item)
                        if cleaned:
                            items.append(cleaned)
                if items:
                    return _filter_valid_memories(items)
    except (json.JSONDecodeError, ValueError):
        pass

    # 4. 按行兜底
    lines = []
    for line in text.splitlines():
        stripped = line.strip()

        # 去掉行首的列表标记
        stripped = re.sub(r'^[-*]\s*', '', stripped)
        stripped = re.sub(r'^\d+[.\、)]\s*', '', stripped)
        stripped = stripped.strip().strip('"「"」\'')

        if not stripped:
            continue

        # 5. 过滤垃圾行
        if stripped in _GARBAGE_PATTERNS:
            continue

        # 纯符号行（没有字母、中文或日文）
        has_content = bool(re.search(r'[a-zA-Z一-鿿぀-ゟ゠-ヿ]', stripped))
        if not has_content:
            continue

        # 过短无意义行（< 6 字符且无分类标签）
        if len(stripped) < 6 and not _VALID_MEMORY_TAG.search(stripped):
            continue

        lines.append(stripped)

    items = normalize_memory_items(lines)
    return _filter_valid_memories(items)


def _filter_valid_memories(items: list[str]) -> list[str]:
    """过滤：只保留包含分类标签或有明确正文的条目。"""
    import re

    _VALID_MEMORY_TAG = re.compile(
        r"\[(?:hard_fact|relationship|plot_fact|character_state"
        r"|user_preference|temporary_state|world_state|legacy"
        r"|fact|event|promise|preference|secret|goal|scene_state)\]"
    )

    result = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue

        stripped = item.strip()
        if stripped in _GARBAGE_PATTERNS:
            continue

        # 包含有效分类标签 → 直接保留
        if _VALID_MEMORY_TAG.search(item):
            seen.add(item)
            result.append(item)
            continue

        # 没有标签但有中英文正文（>= 15 字符）→ 保留
        has_text = bool(re.search(r'[a-zA-Z一-鿿぀-ゟ゠-ヿ]', item))
        if has_text and len(item) >= 15:
            seen.add(item)
            result.append(item)
            continue

    return result


def normalize_base_url(url: str) -> str:
    """规范化 base_url：去掉尾部斜杠，裁剪 /chat/completions 后缀。

    规则：
    - 去掉尾部 /
    - 如果以 /chat/completions 结尾 → 裁剪掉
    - 如果以 /completions 结尾 → 裁剪掉
    - 保留到 /v1 或其他 base path

    Examples:
        https://api.deepseek.com/chat/completions → https://api.deepseek.com
        https://api.deepseek.com/v1/chat/completions → https://api.deepseek.com/v1
        https://vsllm.com/v1 → https://vsllm.com/v1
        https://open.bigmodel.cn/api/paas/v4/chat/completions → https://open.bigmodel.cn/api/paas/v4
    """
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")]
    elif url.endswith("/completions"):
        url = url[:-len("/completions")]
    return url


def build_chat_url(base_url: str) -> str:
    """从 base_url 构建 chat completions 端点。"""
    base = normalize_base_url(base_url)
    return base + "/chat/completions"


def build_models_url(base_url: str) -> str:
    """从 base_url 构建 models 端点。"""
    base = normalize_base_url(base_url)
    return base + "/models"


def filter_sensitive(text: str) -> str:
    """统一敏感信息过滤：API Key、Token、密码等。

    自动从 .env 和 providers.yaml 读取所有可能的 secret，
    以及 sk- 开头的 key、Bearer token、Authorization header。
    所有日志展示、Web 异常页、Provider 测试错误都必须调用此函数。
    """
    import os
    import re

    if not text or not isinstance(text, str):
        return text

    # 收集所有需要过滤的 secret
    secrets = set()

    # 1. 从 .env 读取常见环境变量
    env_keys = [
        "BOT_TOKEN", "WEB_PASSWORD",
        "DEEPSEEK_KEY", "DEEPSEEK_API_KEY",
        "ZHIPU_API_KEY", "OPENROUTER_API_KEY",
        "VSLLM_API_KEY", "MINIMAX_API_KEY",
        "KIMI_API_KEY", "CATALW_API_KEY",
    ]
    for key in env_keys:
        val = os.getenv(key, "").strip()
        if val and len(val) > 4:
            secrets.add(val)

    # 2. 从 providers.yaml 读取 api_key_env 对应的环境变量
    try:
        yaml_path = settings.BASE_DIR / "providers.yaml"
        if yaml_path.exists():
            import yaml
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for p in data.get("providers", []):
                    if isinstance(p, dict):
                        env_name = p.get("api_key_env", "")
                        if env_name:
                            val = os.getenv(env_name, "").strip()
                            if val and len(val) > 4:
                                secrets.add(val)
    except Exception:
        pass

    result = text

    # 3. 过滤完整 secret 值
    for secret in sorted(secrets, key=len, reverse=True):
        result = result.replace(secret, "***")

    # 4. 过滤 Telegram Bot API URL 和 token 形态
    result = re.sub(r'/bot\d{8,10}:[a-zA-Z0-9_-]{25,}/', '/bot***/', result)
    result = re.sub(r'\d{8,10}:[a-zA-Z0-9_-]{25,}', '***:***', result)

    # 5. 过滤 sk- 开头的 key
    result = re.sub(r'sk-[a-zA-Z0-9_-]{8,}', 'sk-***', result)

    # 6. 过滤 Authorization / Bearer token
    result = re.sub(r'Authorization:\s*Bearer\s+[a-zA-Z0-9_\-\.=]+', 'Authorization: Bearer ***', result, flags=re.I)
    result = re.sub(r'Bearer\s+[a-zA-Z0-9_\-\.=]+', 'Bearer ***', result)

    # 7. 过滤非 Bearer 的 Authorization header 值
    result = re.sub(r'Authorization[:\s]+(?!Bearer\b)[a-zA-Z0-9_\-\.=]+', 'Authorization: ***', result, flags=re.I)

    # 8. 过滤 Cookie header 值
    result = re.sub(r'Cookie[:\s]+[^,\r\n;]+(?:;[^,\r\n]+)*', 'Cookie: ***', result, flags=re.I)

    return result


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
