import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

import requests

from config import settings
from bot.utils import get_reply_length


logger = logging.getLogger(__name__)
_usage_lock = threading.Lock()


def _log_usage(purpose: str, success: bool, usage: dict | None = None, error: str = "") -> None:
    """记录 API 用量到 logs/api_usage.jsonl。"""
    if not settings.API_USAGE_LOG_ENABLED:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": settings.MODEL_NAME,
        "purpose": purpose,
        "success": success,
    }
    if usage:
        entry["prompt_tokens"] = usage.get("prompt_tokens", 0)
        entry["completion_tokens"] = usage.get("completion_tokens", 0)
        entry["total_tokens"] = usage.get("total_tokens", 0)
    if error:
        entry["error"] = error[:200]
    try:
        log_dir = settings.BASE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with _usage_lock:
            with open(log_dir / "api_usage.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class DeepSeekClient:
    """DeepSeek API 的轻量封装。

    项目暂时不引入异步 HTTP 库，使用 requests + asyncio.to_thread，
    既保持依赖简单，也不会阻塞 Telegram Bot 的事件循环。
    """

    def __init__(self, api_key: str, model_name: str):
        self.api_key = api_key
        self.model_name = model_name

    async def chat(
        self,
        messages: list,
        max_tokens: int = None,
        temperature: float = 0.85,
        purpose: str = "main_chat",
    ) -> tuple[str, str]:
        """调用 DeepSeek API 进行对话补全。

        Args:
            messages: OpenAI 格式的消息列表 [{"role": ..., "content": ...}, ...]
            max_tokens: 最大生成 token 数，None 时使用随机长度（见 get_reply_length）
            temperature: 生成温度（0~2）
            purpose: 调用用途标签（main_chat/continue/memory_extract/...）

        Returns:
            (reply_text, finish_reason) — reply_text 已 strip
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens if max_tokens is not None else get_reply_length(),
        }

        last_error: Exception | None = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(
                    requests.post,
                    settings.DEEPSEEK_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                reply = choice["message"]["content"].strip()
                finish_reason = choice.get("finish_reason")
                _log_usage(purpose, True, data.get("usage"))
                logger.info("DeepSeek API call succeeded (%s)", purpose)
                return reply, finish_reason
            except requests.exceptions.Timeout as exc:
                last_error = exc
                logger.warning("DeepSeek API timeout (%s/%s)", attempt + 1, max_retries)
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                logger.warning("DeepSeek API connection error (%s/%s)", attempt + 1, max_retries)
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else 0
                if 400 <= status < 500 and status != 429:
                    logger.error("DeepSeek API HTTP %s — 不可重试，中止", status)
                    break
                logger.warning("DeepSeek API HTTP %s (%s/%s)", status, attempt + 1, max_retries)
            except (ValueError, KeyError, TypeError) as exc:
                last_error = exc
                logger.error("DeepSeek API 响应格式异常 — 不可重试，中止: %s", exc)
                break

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        _log_usage(purpose, False, error=str(last_error)[:200])
        raise RuntimeError("无法连接到 DeepSeek API，请稍后再试。") from last_error
