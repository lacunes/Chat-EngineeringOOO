"""
World template: two

复制本文件即可创建新世界。
文件名、WORLD_NAME、ACTIVE_WORLD 三者建议保持一致。
"""

WORLD_NAME = "two"

# 开场文本会在 /start 时发送给用户。
START_SCENE = (
    "世界 two 已启动。\n\n"
    "这里是开场场景占位文本。请在 worlds/two.py 中替换为你的正式开场。"
)

# SYSTEM_PROMPT 是这个世界最核心的设定入口。
# 世界观、人物、关系、规则、叙事风格都写在这里即可。
SYSTEM_PROMPT = """
你是世界 two 的角色扮演叙事者。

用户扮演自己的角色，你负责旁白、环境、NPC 和剧情反馈。
不要替用户角色说话、行动或做决定。
每轮结尾都要留出用户可以回应的空间。

这里是系统提示词占位文本。
请在 worlds/two.py 中替换为你的正式世界观、人物关系、叙事风格和剧情规则。
""".strip()

# ── 以下为可选扩展属性 ──
# 如果不需要可以先留空，不会影响 Bot 运行。
# 未来版本可能会自动读取这些字段来丰富叙事。

CHARACTERS: dict[str, str] = {
    # "角色名": "角色简要描述",
}

RULES: list[str] = [
    # "自定义规则 1",
    # "自定义规则 2",
]

LOCATIONS: dict[str, str] = {
    # "地点名": "地点描述",
}

EVENT_POOL: list[str] = [
    # "可能触发的随机事件描述",
]
