import logging
import re
import sys
import threading
import time
import traceback

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.deepseek_client import DeepSeekClient
from bot.llm_router import LLMRouter
from bot.memory_manager import MemoryManager
from bot.relationship_manager import RelationshipManager
from bot.telegram_handlers import RoleplayBot
from bot.time_manager import TimeManager
from bot.world_manager import WorldManager
from bot.utils import load_world
from config import settings
from web.app import AppContext, create_app


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

    # 根 logger：app.log + 终端
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    # 主日志文件（INFO+）
    app_handler = logging.FileHandler(settings.LOG_FILE, encoding="utf-8")
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(fmt)
    root.addHandler(app_handler)

    # 错误日志文件（ERROR+）
    err_handler = logging.FileHandler(settings.LOG_ERROR_FILE, encoding="utf-8")
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)
    root.addHandler(err_handler)

    # 终端输出
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ── 专题日志 ──
    _add_specialty_logger("bot.memory", settings.LOG_MEMORY_FILE, fmt)
    _add_specialty_logger("bot.relation", settings.LOG_RELATION_FILE, fmt)
    _add_specialty_logger("bot.story", settings.LOG_STORY_FILE, fmt)
    _add_specialty_logger("security", settings.LOG_SECURITY_FILE, fmt)


def _add_specialty_logger(name: str, path, fmt) -> logging.Logger:
    """创建写入专属文件的 logger，不传播到根 logger（避免重复输出）。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 不传播到根 logger，避免重复写 app.log
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


def validate_settings() -> None:
    # 启动前检查必要配置，避免 Bot 跑起来后才报错。
    missing = []
    if not settings.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not settings.ALLOWED_ID:
        missing.append("ALLOWED_ID")
    # 至少需要一个可用的 API Key
    has_api_key = bool(settings.DEEPSEEK_KEY or settings.DEEPSEEK_API_KEY
                       or settings.ZHIPU_API_KEY or settings.OPENROUTER_API_KEY)
    if not has_api_key:
        missing.append("至少需要一个 API Key (DEEPSEEK_API_KEY / ZHIPU_API_KEY / OPENROUTER_API_KEY)")
    if missing:
        raise RuntimeError(f"Missing required settings in .env: {', '.join(missing)}")


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


def set_bot_commands() -> None:
    # 注册 Telegram Bot 菜单，让手机端输入命令更方便。
    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/setMyCommands"
    payload = {
        "commands": [
            {"command": cmd, "description": desc}
            for cmd, desc in COMMANDS.items()
        ]
    }
    try:
        requests.post(url, json=payload, timeout=10)
        logging.getLogger(__name__).info("Telegram command menu updated")
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to set command menu: %s", exc)


# ── 全局错误处理器 ──────────────────────────────────

# 敏感信息过滤正则（防止泄露到日志）
_SENSITIVE_FILTERS = [
    (re.compile(r'sk-[a-zA-Z0-9]{10,}'), 'sk-***'),
    (re.compile(r'\d{8,10}:[a-zA-Z0-9_-]{25,}'), '***:***'),
]


def _filter_sensitive(text: str) -> str:
    for pattern, replacement in _SENSITIVE_FILTERS:
        text = pattern.sub(replacement, text)
    # 额外：显式过滤 .env 中的敏感值
    for secret in [settings.BOT_TOKEN, settings.DEEPSEEK_KEY, settings.DEEPSEEK_API_KEY,
                   settings.ZHIPU_API_KEY, settings.OPENROUTER_API_KEY, settings.WEB_PASSWORD]:
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

    roleplay_bot = RoleplayBot(world_manager, client)

    set_bot_commands()

    app = ApplicationBuilder().token(settings.BOT_TOKEN).build()

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
    ctx = AppContext(world_manager, roleplay_bot, client, time.time())
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
