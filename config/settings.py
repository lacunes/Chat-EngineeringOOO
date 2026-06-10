"""
运行时配置 —— 所有可调参数均从 .env 文件读取。

每个配置项都附带详细注释，解释：
- 这个数字控制什么
- 为什么是这个默认值
- 调高或调低会有什么影响

修改 .env 后重启 Bot 即可生效，无需改代码。
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


logger = logging.getLogger(__name__)

# 项目根目录。后续所有相对路径都从这里出发，方便 VPS 部署。
BASE_DIR = Path(__file__).resolve().parent.parent

_env_path = BASE_DIR / ".env"
if not _env_path.exists():
    # 延迟导入避免循环引用；此时 logging 还未初始化，用 print 兜底。
    print(f"[WARNING] .env file not found at {_env_path!s}，请复制 .env.example 并填入配置。")

load_dotenv(_env_path)


def _get_int(name: str, default: int) -> int:
    """从环境变量读取整数，空值或缺失时返回默认值。"""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


# ═══════════════════════════════════════════════════════════════
# 敏感配置 —— 必须填入真实值
# ═══════════════════════════════════════════════════════════════

# Telegram Bot Token，从 @BotFather 获取。
# 格式: 数字:英文数字组合，如 "1234567890:ABCdef..."
# strip() 防止复制粘贴时带入空格导致鉴权失败。
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# DeepSeek API Key，从 platform.deepseek.com 获取。
# 格式: "sk-" 开头。
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "").strip()

# 允许使用 Bot 的 Telegram 用户 ID（数字）。
# 设为 0 表示不限制（不安全！）。
# 获取自己的 ID: 给 @userinfobot 发消息。
ALLOWED_ID = _get_int("ALLOWED_ID", 0)

# ═══════════════════════════════════════════════════════════════
# 世界与模型配置
# ═══════════════════════════════════════════════════════════════

# 当前启用的世界，对应 data/worlds/<name>.yaml 文件名。
# 可通过 runtime_state.json 在 Web 面板动态切换（无需重启）。
ACTIVE_WORLD = os.getenv("ACTIVE_WORLD", "one").strip().lower()

# DeepSeek 模型名。
# 默认使用 deepseek-v4-flash（推荐）。
# 旧名 deepseek-chat / deepseek-reasoner 将在 2026-07-24 废弃，仅为兼容映射。
# 可通过 .env 覆盖: MODEL_NAME=deepseek-v4-flash
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-v4-flash")

# DeepSeek 思考模式（thinking）。
# "disabled": 非思考模式，适合角色聊天（默认）
# "enabled":  思考模式，回复前先内部推理，适合复杂逻辑但不建议日常角色聊天
# 如果不兼容当前模型，设为 disabled 或留空即可。
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()

# DeepSeek API 地址。一般不需要改，除非 DeepSeek 变更了 API 域名。
DEEPSEEK_API_URL = os.getenv(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)

# ═══════════════════════════════════════════════════════════════
# 上下文与短期记忆配置
# ═══════════════════════════════════════════════════════════════

# 每次发给模型的最近对话条数。
# 默认 60 条 ≈ 最近 30 轮对话（用户+助手各算1条）。
# 调高 → 模型能看到更久远的上下文，但 token 消耗增加，回复速度变慢。
# 调低 → 模型更快响应，但可能遗忘更早的剧情。
# 注意：DeepSeek 上下文窗口为 128K，60 条对话通常只占 10-20K tokens。
CONTEXT_LENGTH = _get_int("CONTEXT_LENGTH", 60)

# 短期记忆超过此条数时触发压缩（将较早的一半压缩为摘要存入长期记忆）。
# 默认 200 条 ≈ 100 轮对话。
# 调高 → 压缩频率降低，短期记忆更完整，但 API 调用 token 更多。
# 调低 → 更频繁压缩，节省 token，但可能丢失细节。
# 压缩策略：保留后半（100条），前半压缩为摘要。
MEMORY_MAX_LENGTH = _get_int("MEMORY_MAX_LENGTH", 200)

# ═══════════════════════════════════════════════════════════════
# 回复长度配置
# 实际每次回复在 utils.get_reply_length() 中随机取值：
#   85% 概率: [MIN, MID] 之间均匀随机 (700~2400)
#   15% 概率: [MID, MAX] 之间二次方分布 (2400~3400, 偏向较短)
# 二次方分布意味着更长回复出现的概率越来越小，
# 避免频繁生成 3000+ token 的冗长回复。
# ═══════════════════════════════════════════════════════════════

# 最短回复 token 数（700 ≈ 中文约 500-600 字）。
# 调高 → 回复更有内容感，但短问答场景显得啰嗦。
# 调低 → 回复更简洁，但可能太短缺乏描写。
MIN_REPLY_TOKENS = _get_int("MIN_REPLY_TOKENS", 700)

# 中等回复上限 token 数（2400 ≈ 中文约 1800-2000 字）。
# 85% 的回复会在此范围内。这是"正常"回复的软上限。
MID_REPLY_TOKENS = _get_int("MID_REPLY_TOKENS", 2400)

# 最长回复 token 数（3400 ≈ 中文约 2500-2800 字）。
# 15% 的回复会超出 MID 但不超过此值，且概率呈二次方递减。
# 调高 → 偶尔会有很长的精彩描写，但可能超出 Telegram 单条消息限制。
MAX_REPLY_TOKENS = _get_int("MAX_REPLY_TOKENS", 3400)

# ═══════════════════════════════════════════════════════════════
# Telegram 消息分段配置
# ═══════════════════════════════════════════════════════════════

# 单条消息分段阈值（字符数，不是 token 数）。
# 默认 3000 字符 ≈ Telegram 消息的舒适阅读上限。
# 超过此值时，split_reply() 会在句号、换行等自然断点处切开。
# 调高 → 更少分段，但单条消息可能过长影响手机阅读。
# 调低 → 更多分段，发送条数增多，体验碎片化。
SPLIT_THRESHOLD = _get_int("SPLIT_THRESHOLD", 3000)

# /c 续写命令的单次最大续写次数（默认 7 次）。
# 设为 7 的原因：一次续写约 2000 字，7 次 = 14000 字，足以推进剧情。
# 过低（如 3）→ 手动档体验，用户要频繁输入 /c。
# 过高（如 20）→ 模型可能跑偏很远，且用户难以打断。
CONTINUE_LIMIT = _get_int("CONTINUE_LIMIT", 7)

# ═══════════════════════════════════════════════════════════════
# 长期记忆配置
# ═══════════════════════════════════════════════════════════════

# /memo 命令允许的最大字数（500 字）。
# 手写记忆通常是一两句话，500 字足够描述一个复杂事件。
# 设得太高 → 用户可能写入过大文本，污染长期记忆。
MEMO_SIZE_LIMIT = _get_int("MEMO_SIZE_LIMIT", 500)

# 注入到每次模型调用的最近长期记忆条数（12 条）。
# 长期记忆是故事中的"稳定事实"，注入太多会占用上下文窗口。
# 调高 → 模型能记住更多设定，但 token 消耗增加。
# 调低 → 更省 token，但模型可能遗忘重要设定。
LONG_MEMORY_CONTEXT_LIMIT = _get_int("LONG_MEMORY_CONTEXT_LIMIT", 12)

# 长期记忆精炼后的最大保留条数（12 条）。
# 精炼会将多条相似记忆合并去重，最终保留最多 12 条核心信息。
# 设为 12 的原因：足够覆盖核心角色关系、地点、关键事件，又不会过度膨胀。
# 调高 → 保留更多细节，但长期记忆文件变大，注入 prompt 也更多。
LONG_MEMORY_MAX_ITEMS = _get_int("LONG_MEMORY_MAX_ITEMS", 12)

# 自动记忆抽取间隔（每 26 条消息触发一次）。
# 26 条 ≈ 13 轮对话，在这之间通常会有一些值得记录的关系/事件变化。
# 调高 → 抽取频率降低，可能遗漏短期内的多个重要变化。
# 调低 → 抽取太频繁，API 调用增加且可能抽出大量冗余信息。
AUTO_MEMORY_INTERVAL = _get_int("AUTO_MEMORY_INTERVAL", 26)

# 自动抽取时回顾的最近消息数（32 条）。
# 略大于 AUTO_MEMORY_INTERVAL(26)，确保覆盖区间有少许重叠不会遗漏。
# 调高 → 抽取时看到更广的上下文，但 token 消耗更多。
# 调低 → 更快、更省 token，但可能漏掉跨越较长对话的伏笔。
AUTO_MEMORY_LOOKBACK = _get_int("AUTO_MEMORY_LOOKBACK", 32)

# 长期记忆精炼的缓冲条数（超出 MAX_ITEMS 此值才触发精炼）。
# 默认: 12 (MAX) + 4 (BUFFER) = 16 条时触发精炼，精炼后回到 12 条。
# 设为 4 的原因：给新记忆留出累积空间，不要太频繁触发精炼 API 调用。
# 调高 → 精炼间隔更长，API 调用更少但记忆列表更长。
# 调低 → 精炼更频繁，记忆列表始终紧凑但 API 调用增加。
LONG_MEMORY_REFINE_BUFFER = _get_int("LONG_MEMORY_REFINE_BUFFER", 20)
LONG_MEMORY_EXTRACT_REQUIRE_SIGNAL = os.getenv("LONG_MEMORY_EXTRACT_REQUIRE_SIGNAL", "true").strip().lower() != "false"

# ═══════════════════════════════════════════════════════════════
# 记忆提醒配置
# ═══════════════════════════════════════════════════════════════

# /memo 手动记忆提醒间隔（每 40 条消息提醒一次）。
# 40 条 ≈ 20 轮对话，足够发展出值得记录的新关系或事件。
# 设为 0 可完全禁用提醒。
# 调高 → 提醒更少，用户体验更干净但可能忘记记录。
# 调低 → 提醒更频繁，可能造成干扰。
MEMO_REMINDER_INTERVAL = _get_int("MEMO_REMINDER_INTERVAL", 40)

# ═══════════════════════════════════════════════════════════════
# 重置确认配置
# ═══════════════════════════════════════════════════════════════

# /reset 二次确认的超时秒数（30 秒）。
# 在此时限内再次发送 /reset 确认清空记忆。
# 设为 30 的原因：足够用户阅读提示并决定是否确认，但不会太长。
# 调高 → 更宽容的确认窗口。
# 调低 → 更快的误触保护，但用户可能来不及确认。
RESET_CONFIRM_SECONDS = _get_int("RESET_CONFIRM_SECONDS", 30)

# ═══════════════════════════════════════════════════════════════
# NPC主动行为系统配置
# ═══════════════════════════════════════════════════════════════

NPC_BASE_ACTIVATION = float(os.getenv("NPC_BASE_ACTIVATION", "0.5"))
NPC_MAX_ACTIONS_PER_CHECK = _get_int("NPC_MAX_ACTIONS_PER_CHECK", 1)
NPC_TIMER_INTERVAL = _get_int("NPC_TIMER_INTERVAL", 300)
NPC_ACTION_MAX_TOKENS = _get_int("NPC_ACTION_MAX_TOKENS", 200)
NPC_TIMER_ACTIVATION_MULTIPLIER = float(os.getenv("NPC_TIMER_ACTIVATION_MULTIPLIER", "0.6"))
NPC_CONTEXT_BOOST_MULTIPLIER = float(os.getenv("NPC_CONTEXT_BOOST_MULTIPLIER", "2.0"))

# ═══════════════════════════════════════════════════════════════
# 关系网络配置
# ═══════════════════════════════════════════════════════════════

RELATION_EXTRACT_INTERVAL = _get_int("RELATION_EXTRACT_INTERVAL", 2)
RELATION_SIGNIFICANT_THRESHOLD = _get_int("RELATION_SIGNIFICANT_THRESHOLD", 3)
RELATION_EXTRACT_REQUIRE_SIGNAL = os.getenv("RELATION_EXTRACT_REQUIRE_SIGNAL", "true").strip().lower() != "false"

# ═══════════════════════════════════════════════════════════════
# 时间流逝配置
# ═══════════════════════════════════════════════════════════════

# 是否启用机械式自动推进（默认关闭，改为用户驱动）。
TIME_AUTO_ADVANCE_ENABLED = os.getenv("TIME_AUTO_ADVANCE_ENABLED", "false").strip().lower() == "true"

# 是否启用用户消息关键词检测推进（默认开启）。
TIME_USER_DRIVEN_ADVANCE_ENABLED = os.getenv("TIME_USER_DRIVEN_ADVANCE_ENABLED", "true").strip().lower() != "false"

# 同个时段超过 N 轮时，在回复末尾追加温和提示（默认 80）。
TIME_LONG_SCENE_HINT_THRESHOLD = _get_int("TIME_LONG_SCENE_HINT_THRESHOLD", 80)

# 普通时段推进（非跨天关键词）是否允许从深夜跨到第二天（默认 false）。
TIME_AUTO_CROSS_DAY = os.getenv("TIME_AUTO_CROSS_DAY", "false").strip().lower() == "true"

# ═══════════════════════════════════════════════════════════════
# 后台维护与API优化配置
# ═══════════════════════════════════════════════════════════════

BACKGROUND_MAINTENANCE_COOLDOWN_SECONDS = _get_int("BACKGROUND_MAINTENANCE_COOLDOWN_SECONDS", 60)
API_USAGE_LOG_ENABLED = os.getenv("API_USAGE_LOG_ENABLED", "true").strip().lower() != "false"
COMBINED_MAINTENANCE_ENABLED = os.getenv("COMBINED_MAINTENANCE_ENABLED", "false").strip().lower() == "true"

# ═══════════════════════════════════════════════════════════════
# Web 管理面板配置
# ═══════════════════════════════════════════════════════════════

# Web 面板监听端口（默认 8080）。
WEB_PORT = _get_int("WEB_PORT", 8080)

# Web 面板监听地址。默认 0.0.0.0 监听所有网络接口（可通过外网 IP 访问）。
# 如果只想本地访问（配合 SSH 隧道），改为 127.0.0.1。
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")

# Web 面板登录密码。用户名固定为 admin。
# 留空则面板不设密码（不安全！强烈建议设置）。
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()

# ═══════════════════════════════════════════════════════════════
# 文件路径配置
# ═══════════════════════════════════════════════════════════════

LOG_FILE = BASE_DIR / "logs" / "app.log"
MEMORY_DIR = BASE_DIR / "memory"

# 分层日志路径
LOG_DIR = BASE_DIR / "logs"
LOG_ERROR_FILE = LOG_DIR / "error.log"
LOG_MEMORY_FILE = LOG_DIR / "memory.log"
LOG_RELATION_FILE = LOG_DIR / "relation.log"
LOG_STORY_FILE = LOG_DIR / "story.log"
LOG_SECURITY_FILE = LOG_DIR / "security.log"

# ═══════════════════════════════════════════════════════════════
# 多模型供应商配置（LLM Router）
# ═══════════════════════════════════════════════════════════════

# providers.yaml 路径（非敏感 provider 配置）
PROVIDERS_YAML_PATH = BASE_DIR / "providers.yaml"

# provider_state.json 路径（运行时状态持久化）
PROVIDER_STATE_PATH = BASE_DIR / "data" / "provider_state.json"

# 新增 API Key 环境变量（从 .env 读取，不在 providers.yaml 中）
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

# 兼容旧变量：DEEPSEEK_KEY → DEEPSEEK_API_KEY（如果新变量未设置则回退到旧变量）
if not DEEPSEEK_API_KEY and DEEPSEEK_KEY:
    DEEPSEEK_API_KEY = DEEPSEEK_KEY
