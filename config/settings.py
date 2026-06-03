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
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


# 这些敏感配置只从 .env 读取，不写进代码。
# strip() 防止复制粘贴时带进多余空格。
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "").strip()
ALLOWED_ID = _get_int("ALLOWED_ID", 0)

# 当前启用世界。比如 ACTIVE_WORLD=one 会加载 worlds/one.py。
ACTIVE_WORLD = os.getenv("ACTIVE_WORLD", "one").strip().lower()

# DeepSeek 模型配置。
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)

# 上下文与短期记忆压缩配置。
CONTEXT_LENGTH = _get_int("CONTEXT_LENGTH", 60)
MEMORY_MAX_LENGTH = _get_int("MEMORY_MAX_LENGTH", 200)

# 回复长度配置。实际每次回复会在 utils.get_reply_length 中随机取值。
MIN_REPLY_TOKENS = _get_int("MIN_REPLY_TOKENS", 700)
MID_REPLY_TOKENS = _get_int("MID_REPLY_TOKENS", 2400)
MAX_REPLY_TOKENS = _get_int("MAX_REPLY_TOKENS", 3400)

# Telegram 单条消息太长会影响阅读，因此超过阈值会自动分段。
SPLIT_THRESHOLD = _get_int("SPLIT_THRESHOLD", 3000)
CONTINUE_LIMIT = _get_int("CONTINUE_LIMIT", 7)

# 长期记忆配置。
MEMO_SIZE_LIMIT = _get_int("MEMO_SIZE_LIMIT", 500)
LONG_MEMORY_CONTEXT_LIMIT = _get_int("LONG_MEMORY_CONTEXT_LIMIT", 12)
LONG_MEMORY_MAX_ITEMS = _get_int("LONG_MEMORY_MAX_ITEMS", 12)
AUTO_MEMORY_INTERVAL = _get_int("AUTO_MEMORY_INTERVAL", 26)
AUTO_MEMORY_LOOKBACK = _get_int("AUTO_MEMORY_LOOKBACK", 32)
LONG_MEMORY_REFINE_BUFFER = _get_int("LONG_MEMORY_REFINE_BUFFER", 4)

RESET_CONFIRM_SECONDS = _get_int("RESET_CONFIRM_SECONDS", 30)

# ── NPC主动行为系统配置 ──
# NPC_BASE_ACTIVATION: 全局基础激活概率（0~1），作用于所有NPC的权重。
#   例如 NPC 权重 0.3 × 全局 0.5 = 每次消息 15% 实际触发概率。
#   调高 → NPC更活跃；调低 → NPC更沉默。设为 0 可完全禁用NPC主动行为。
NPC_BASE_ACTIVATION = float(os.getenv("NPC_BASE_ACTIVATION", "0.5"))
# NPC_MAX_ACTIONS_PER_CHECK: 单次检查最多触发几个NPC（防止多个NPC同时行动造成混乱）
NPC_MAX_ACTIONS_PER_CHECK = _get_int("NPC_MAX_ACTIONS_PER_CHECK", 1)
# NPC_TIMER_INTERVAL: 后台定时器检查间隔（秒），用于时间驱动的NPC行为
NPC_TIMER_INTERVAL = _get_int("NPC_TIMER_INTERVAL", 300)
# NPC_ACTION_MAX_TOKENS: NPC行为舞台指令的最大token数（实际使用时截断）
NPC_ACTION_MAX_TOKENS = _get_int("NPC_ACTION_MAX_TOKENS", 200)

LOG_FILE = BASE_DIR / "bot.log"
MEMORY_DIR = BASE_DIR / "memory"
