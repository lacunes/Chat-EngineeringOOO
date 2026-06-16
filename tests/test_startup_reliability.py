import io
import asyncio
import logging
from types import SimpleNamespace

from main import SensitiveDataFilter, set_bot_commands


class FakeBot:
    def __init__(self, exc=None):
        self.exc = exc
        self.commands = None

    async def set_my_commands(self, commands, **kwargs):
        self.commands = commands
        if self.exc:
            raise self.exc
        return True


def test_set_bot_commands_success():
    bot = FakeBot()
    ok = asyncio.run(set_bot_commands(SimpleNamespace(bot=bot)))

    assert ok is True
    assert bot.commands
    assert bot.commands[0].command == "c"


def test_set_bot_commands_failure_does_not_raise():
    bot = FakeBot(exc=TimeoutError("connect timeout"))
    ok = asyncio.run(set_bot_commands(SimpleNamespace(bot=bot)))

    assert ok is False


def test_sensitive_data_filter_sanitizes_handler_output(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456789:abcdefghijklmnopqrstuvwxyzABC")
    monkeypatch.setenv("WEB_PASSWORD", "web-secret-password")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-secret-value")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SensitiveDataFilter())

    logger = logging.getLogger("test.sensitive_filter")
    logger.handlers = []
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info(
        "url=https://api.telegram.org/bot%s/sendMessage Authorization: Bearer %s Cookie: session=abc password=%s",
        "123456789:abcdefghijklmnopqrstuvwxyzABC",
        "sk-real-secret-value",
        "web-secret-password",
    )

    output = stream.getvalue()
    assert "123456789:abcdefghijklmnopqrstuvwxyzABC" not in output
    assert "sk-real-secret-value" not in output
    assert "web-secret-password" not in output
    assert "/bot***/sendMessage" in output
    assert "Bearer ***" in output
    assert "Cookie: ***" in output
