import logging
import logging.handlers
import os
import re
import sys
import threading
import time
import traceback

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.deepseek_client import DeepSeekClient
from bot.event_bus import EventBus
from bot.llm_router import LLMRouter
from bot.memory_manager import MemoryManager
from bot.relationship_manager import RelationshipManager
from bot.telegram_handlers import RoleplayBot
from bot.time_manager import TimeManager
from bot.world_manager import WorldManager
from bot.utils import load_world
from config import settings
from web.app import AppContext, create_app


class SensitiveDataFilter(logging.Filter):
    """在 handler 层统一过滤敏感信息，覆盖第三方 logger 输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        from bot.utils import filter_sensitive

        try:
            record.msg = filter_sensitive(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging() -> None:
    """配置分层日志系统。

    logs/app.log     — 全部日志（INFO+）
    logs/error.log   — 仅错误（ERROR+）
    logs/memory.log  — 记忆相关
    logs/relation.log— 关系相关
    logs/story.log   — 剧情状态相关
    logs/security.log— 安全检查相关

    同时保留终端输出，方便 tmux 排查。
    """
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if getattr(root, "_roleplay_logging_configured", False):
        return

    # 根 logger：app.log + 终端
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    sensitive_filter = SensitiveDataFilter()

    # ── 日志轮转配置 ──
    # app.log：单文件最大 5MB，保留 5 个备份
    APP_LOG_MAX_BYTES = 5 * 1024 * 1024
    APP_LOG_BACKUP_COUNT = 5
    # error.log：单文件最大 2MB，保留 10 个备份
    ERROR_LOG_MAX_BYTES = 2 * 1024 * 1024
    ERROR_LOG_BACKUP_COUNT = 10
    # 专题日志（memory/relation/story/security）：单文件最大 2MB，保留 5 个备份
    SPECIALTY_MAX_BYTES = 2 * 1024 * 1024
    SPECIALTY_BACKUP_COUNT = 5

    # 主日志文件（INFO+），使用 RotatingFileHandler 自动轮转
    app_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_FILE, encoding="utf-8",
        maxBytes=APP_LOG_MAX_BYTES, backupCount=APP_LOG_BACKUP_COUNT,
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(fmt)
    app_handler.addFilter(sensitive_filter)
    root.addHandler(app_handler)

    # 错误日志文件（ERROR+），使用 RotatingFileHandler 自动轮转
    err_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_ERROR_FILE, encoding="utf-8",
        maxBytes=ERROR_LOG_MAX_BYTES, backupCount=ERROR_LOG_BACKUP_COUNT,
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)
    err_handler.addFilter(sensitive_filter)
    root.addHandler(err_handler)

    # 终端输出
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    console.addFilter(sensitive_filter)
    root.addHandler(console)

    # ── 专题日志 ──
    _add_specialty_logger("bot.memory", settings.LOG_MEMORY_FILE, fmt, sensitive_filter,
                          max_bytes=SPECIALTY_MAX_BYTES, backup_count=SPECIALTY_BACKUP_COUNT)
    _add_specialty_logger("bot.relation", settings.LOG_RELATION_FILE, fmt, sensitive_filter,
                          max_bytes=SPECIALTY_MAX_BYTES, backup_count=SPECIALTY_BACKUP_COUNT)
    _add_specialty_logger("bot.story", settings.LOG_STORY_FILE, fmt, sensitive_filter,
                          max_bytes=SPECIALTY_MAX_BYTES, backup_count=SPECIALTY_BACKUP_COUNT)
    _add_specialty_logger("security", settings.LOG_SECURITY_FILE, fmt, sensitive_filter,
                          max_bytes=SPECIALTY_MAX_BYTES, backup_count=SPECIALTY_BACKUP_COUNT)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    root._roleplay_logging_configured = True


def _add_specialty_logger(name: str, path, fmt, sensitive_filter: logging.Filter,
                         max_bytes: int = 2 * 1024 * 1024,
                         backup_count: int = 5) -> logging.Logger:
    """创建写入专属文件的 logger，使用 RotatingFileHandler 自动轮转，不传播到根 logger。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 不传播到根 logger，避免重复写 app.log
    if getattr(logger, "_roleplay_logging_configured", False):
        return logger
    handler = logging.handlers.RotatingFileHandler(
        path, encoding="utf-8",
        maxBytes=max_bytes, backupCount=backup_count,
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)
    handler.addFilter(sensitive_filter)
    logger.addHandler(handler)
    logger._roleplay_logging_configured = True
    return logger


def validate_settings() -> None:
    # 启动前检查必要配置，避免 Bot 跑起来后才报错。
    missing = []
    if not settings.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not settings.ALLOWED_ID:
        missing.append("ALLOWED_ID")

    # ── 动态读取 providers.yaml，检查是否至少有一个 enabled provider 的 API Key 有效 ──
    providers_path = settings.BASE_DIR / "providers.yaml"
    if not providers_path.exists():
        missing.append("providers.yaml — 文件不存在")
    else:
        try:
            import yaml as _yaml
            raw = _yaml.safe_load(providers_path.read_text(encoding="utf-8"))
            provider_list = raw.get("providers", []) if isinstance(raw, dict) else []
        except Exception:
            raise RuntimeError("providers.yaml 解析失败，请检查文件格式。")

        enabled = [p for p in provider_list if p.get("enabled", False)]
        if not enabled:
            missing.append("providers.yaml — 没有任何 enabled: true 的 provider")

        any_key_valid = False
        detail_lines = []
        for p in enabled:
            name = p.get("name", "?")
            env_var = p.get("api_key_env", "")
            key_val = os.getenv(env_var, "").strip() if env_var else ""
            has_key = bool(key_val)
            if has_key:
                any_key_valid = True
            else:
                detail_lines.append(f"  {name} → 需要 {env_var}（当前缺失）")

        if not any_key_valid:
            error_msg = "所有启用的 provider 均缺少 API Key：\n" + "\n".join(detail_lines)
            error_msg += "\n请在 .env 中至少设置一个对应的 API Key。"
            raise RuntimeError(error_msg)

        # 兼容旧变量检查：仍提示但不报错
        old_key_vars = []
        if not settings.DEEPSEEK_API_KEY and not settings.DEEPSEEK_KEY:
            old_key_vars.append("DEEPSEEK_API_KEY / DEEPSEEK_KEY")
        if not settings.ZHIPU_API_KEY:
            old_key_vars.append("ZHIPU_API_KEY")
        if not settings.OPENROUTER_API_KEY:
            old_key_vars.append("OPENROUTER_API_KEY")
        if old_key_vars:
            logging.getLogger(__name__).info(
                "部分旧 API Key 变量未设置：%s — 如果有对应 provider 启用则需要配置",
                ", ".join(old_key_vars),
            )

    if missing:
        raise RuntimeError(f"Missing required settings in .env: {', '.join(missing)}")

    # ── Web 安全：公网监听必须设密码 ──
    if settings.WEB_HOST == "0.0.0.0" and not settings.WEB_PASSWORD:
        raise RuntimeError(
            "WEB_HOST=0.0.0.0 但未设置 WEB_PASSWORD。\n"
            "公网监听时必须在 .env 中设置 WEB_PASSWORD 以保护管理面板。"
        )


# ── 命令注册表 ──
# 所有命令的名称和描述集中定义，后续 set_bot_commands() 和 main() 各取所需，
# 新增命令只需要改这一处。
COMMANDS = {
    # 高频命令靠前
    "c":          "续写",
    "continue":   "继续上一段",
    "memo":       "写入长期记忆",
    "refinememo": "精炼长期记忆",
    "status":     "查看状态",
    # 低频命令靠后
    "relations":      "关系摘要",
    "relation_full":  "完整关系",
    "time":           "当前时间",
    "next_time":      "推进时段",
    "next_day":       "推进一天",
    "start":          "启动",
    "reset":          "重开",
}


async def set_bot_commands(application: Application) -> bool:
    """注册 Telegram Bot 菜单。失败只记录 warning，不阻断主流程。"""
    commands = [BotCommand(command=cmd, description=desc) for cmd, desc in COMMANDS.items()]
    try:
        await application.bot.set_my_commands(commands, read_timeout=10, write_timeout=10, connect_timeout=10)
        logging.getLogger(__name__).info("Telegram command menu updated")
        return True
    except TelegramError as exc:
        logging.getLogger(__name__).warning("Failed to set command menu: %s", exc)
    except OSError as exc:
        logging.getLogger(__name__).warning("Failed to set command menu: %s", exc)
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to set command menu: %s", exc)
    return False


# ── 全局错误处理器 ──────────────────────────────────

# 敏感信息过滤正则（防止泄露到日志）
_SENSITIVE_FILTERS = [
    (re.compile(r'sk-[a-zA-Z0-9]{10,}'), 'sk-***'),
    (re.compile(r'\d{8,10}:[a-zA-Z0-9_-]{25,}'), '***:***'),
]


def _filter_sensitive(text: str) -> str:
    for pattern, replacement in _SENSITIVE_FILTERS:
        text = pattern.sub(replacement, text)
    # 显式过滤 .env 中的敏感值
    secrets_to_filter = [settings.BOT_TOKEN, settings.WEB_PASSWORD]
    # 动态读取 providers.yaml 中的 API Key 值
    try:
        import yaml as _yaml
        providers_path = settings.BASE_DIR / "providers.yaml"
        if providers_path.exists():
            raw = _yaml.safe_load(providers_path.read_text(encoding="utf-8"))
            for p in raw.get("providers", []):
                env_var = p.get("api_key_env", "")
                if env_var:
                    val = os.getenv(env_var, "").strip()
                    if val:
                        secrets_to_filter.append(val)
    except Exception:
        pass
    # 兼容旧变量
    for key_name in ["DEEPSEEK_KEY", "DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "OPENROUTER_API_KEY"]:
        val = os.getenv(key_name, "").strip()
        if val:
            secrets_to_filter.append(val)
    for secret in secrets_to_filter:
        if secret and len(secret) > 4:
            text = text.replace(secret, "***")
    return text


async def error_handler(update: object | None, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局错误处理器。

    记录完整异常信息（含 traceback 和 update 内容），
    尝试给用户返回温和提示，不导致主进程退出。
    """
    logger = logging.getLogger("telegram.error_handler")

    # 收集异常信息
    exc = context.error
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)

    # 收集 update 摘要
    update_info = "None"
    if update is not None and isinstance(update, Update):
        try:
            update_info = update.to_json()
        except Exception:
            update_info = str(update)

    # 过滤敏感信息后记录完整堆栈
    safe_tb = _filter_sensitive("".join(tb_lines))
    safe_update = _filter_sensitive(update_info)

    logger.error(
        "Unhandled exception in Telegram handler\n"
        "── Update ──\n%s\n"
        "── Traceback ──\n%s",
        safe_update, safe_tb,
    )

    # 尝试给用户返回温和提示
    if update is not None and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="刚才处理消息时出了一点问题，已经记录日志。",
            )
        except Exception:
            # 发送提示失败也不要让错误处理器崩溃
            pass


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        validate_settings()
    except Exception as exc:
        logger.error("Startup failed: %s", exc)
        sys.exit(1)

    # ── 世界管理器（支持热加载和运行时切换）──
    world_manager = WorldManager()
    world = world_manager.get_world()

    client = DeepSeekClient(settings.DEEPSEEK_KEY, settings.MODEL_NAME)

    # ── 初始化 LLM Router（多供应商路由）──
    llm_router = LLMRouter(notify_callback=None)
    client.set_router(llm_router)

    # ── 事件总线（模块解耦）──
    event_bus = EventBus()

    roleplay_bot = RoleplayBot(world_manager, client, event_bus)
    roleplay_bot.last_update_at = None
    roleplay_bot.last_reply_at = None
    roleplay_bot.consecutive_telegram_network_errors = 0

    ctx = AppContext(world_manager, roleplay_bot, client, time.time())

    async def _post_init(application: Application) -> None:
        await set_bot_commands(application)
        ctx.telegram_polling_started = True

    app = ApplicationBuilder().token(settings.BOT_TOKEN).post_init(_post_init).build()

    # ── 设置路由器通知回调 ──
    async def _notify_admin(text: str) -> None:
        try:
            await app.bot.send_message(chat_id=settings.ALLOWED_ID, text=text)
        except Exception:
            pass
    llm_router._notify = _notify_admin

    # 注册全局错误处理器（必须在 add_handler 之前注册）
    app.add_error_handler(error_handler)

    # 命令 → handler 映射（实例方法，必须在 RoleplayBot 创建后定义）
    handler_map = {
        "start":      roleplay_bot.cmd_start,
        "reset":      roleplay_bot.cmd_reset,
        "status":     roleplay_bot.cmd_status,
        "memo":       roleplay_bot.cmd_memo,
        "refinememo": roleplay_bot.cmd_refine_memo,
        "c":             roleplay_bot.cmd_continue,
        "continue":      roleplay_bot.cmd_continue,
        "relations":     roleplay_bot.cmd_relations,
        "relation_full": roleplay_bot.cmd_relation_full,
        "time":          roleplay_bot.cmd_time,
        "next_time":     roleplay_bot.cmd_next_time,
        "next_day":      roleplay_bot.cmd_next_day,
    }
    for cmd in COMMANDS:
        app.add_handler(CommandHandler(cmd, handler_map[cmd]))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, roleplay_bot.handle_chat))

    # ── NPC主动行为系统说明 ──
    # NPC的定时触发已集成在 NPCManager.tick() 中，不需要独立后台任务。
    # tick() 在每条用户消息时被调用，内部会检查时间间隔并自动触发定时评估。
    # 这样可以避免 asyncio 事件循环兼容性问题（尤其是 python-telegram-bot
    # 在不同版本中对事件循环的管理方式不同）。

    # ── 启动 Web 管理面板（守护线程）──
    web_app = create_app(ctx)
    threading.Thread(
        target=lambda: web_app.run(
            host=settings.WEB_HOST,
            port=settings.WEB_PORT,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
        name="web-panel",
    ).start()

    if settings.WEB_PASSWORD:
        logger.info("Web panel: http://%s:%s (admin / ***)", settings.WEB_HOST, settings.WEB_PORT)
    else:
        logger.warning("Web panel: http://%s:%s (无密码！请设置 WEB_PASSWORD)", settings.WEB_HOST, settings.WEB_PORT)

    logger.info("Roleplay Bot started with world: %s", world.WORLD_NAME)
    app.run_polling()


if __name__ == "__main__":
    main()
