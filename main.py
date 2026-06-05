import logging
import sys
import threading
import time

import requests
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.deepseek_client import DeepSeekClient
from bot.memory_manager import MemoryManager
from bot.relationship_manager import RelationshipManager
from bot.telegram_handlers import RoleplayBot
from bot.time_manager import TimeManager
from bot.utils import load_world
from config import settings
from web.app import AppContext, create_app, register_routes


def setup_logging() -> None:
    # 同时输出到终端和 bot.log，VPS/tmux 排查问题会方便很多。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def validate_settings() -> None:
    # 启动前检查必要配置，避免 Bot 跑起来后才报错。
    missing = []
    if not settings.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not settings.DEEPSEEK_KEY:
        missing.append("DEEPSEEK_KEY")
    if not settings.ALLOWED_ID:
        missing.append("ALLOWED_ID")
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


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        validate_settings()
        # 根据 .env 中的 ACTIVE_WORLD 动态导入 worlds/<name>.py。
        world = load_world(settings.ACTIVE_WORLD)
    except Exception as exc:
        logger.error("Startup failed: %s", exc)
        sys.exit(1)

    client = DeepSeekClient(settings.DEEPSEEK_KEY, settings.MODEL_NAME)
    memory = MemoryManager(world.WORLD_NAME)
    relationships = RelationshipManager(world.WORLD_NAME)
    time_mgr = TimeManager(world.WORLD_NAME)
    roleplay_bot = RoleplayBot(world, memory, client, relationships, time_mgr)

    set_bot_commands()

    app = ApplicationBuilder().token(settings.BOT_TOKEN).build()

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
    ctx = AppContext(world, memory, client, roleplay_bot.npc_manager, relationships, time_mgr, time.time())
    web_app = create_app(ctx)
    register_routes(web_app)
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
