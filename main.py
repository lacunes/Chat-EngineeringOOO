import logging
import sys

import requests
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.deepseek_client import DeepSeekClient
from bot.memory_manager import MemoryManager
from bot.telegram_handlers import RoleplayBot
from bot.utils import load_world
from config import settings


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


def set_bot_commands() -> None:
    # 注册 Telegram Bot 菜单，让手机端输入命令更方便。
    commands = [
        ("c", "续写"),
        ("continue", "继续上一段"),
        ("status", "查看状态"),
        ("start", "启动"),
        ("reset", "重开"),
        ("memo", "写入长期记忆"),
        ("refinememo", "精炼长期记忆"),
    ]
    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/setMyCommands"
    payload = {
        "commands": [
            {"command": command, "description": description}
            for command, description in commands
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
    roleplay_bot = RoleplayBot(world, memory, client)

    set_bot_commands()

    app = ApplicationBuilder().token(settings.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", roleplay_bot.cmd_start))
    app.add_handler(CommandHandler("reset", roleplay_bot.cmd_reset))
    app.add_handler(CommandHandler("status", roleplay_bot.cmd_status))
    app.add_handler(CommandHandler("memo", roleplay_bot.cmd_memo))
    app.add_handler(CommandHandler("refinememo", roleplay_bot.cmd_refine_memo))
    app.add_handler(CommandHandler("continue", roleplay_bot.cmd_continue))
    app.add_handler(CommandHandler("c", roleplay_bot.cmd_continue))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, roleplay_bot.handle_chat))

    logger.info("Roleplay Bot started with world: %s", world.WORLD_NAME)
    app.run_polling()


if __name__ == "__main__":
    main()
