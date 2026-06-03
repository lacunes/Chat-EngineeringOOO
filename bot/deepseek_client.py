import asyncio
import logging

import requests

from config import settings
from bot.utils import get_reply_length


logger = logging.getLogger(__name__)


class DeepSeekClient:
    """DeepSeek API 的轻量封装。

    项目暂时不引入异步 HTTP 库，使用 requests + asyncio.to_thread，
    既保持依赖简单，也不会阻塞 Telegram Bot 的事件循环。
    """

    def __init__(self, api_key: str, model_name: str):
        self.api_key = api_key
        self.model_name = model_name

    async def chat(self, messages: list, max_tokens: int = None) -> tuple[str, str]:
        # DeepSeek 的接口与 OpenAI Chat Completions 格式类似。
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.85,
            "max_tokens": max_tokens if max_tokens is not None else get_reply_length(),
        }

        last_error: Exception | None = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # requests 是同步库，所以放到线程里执行，避免卡住整个 Bot。
                response = await asyncio.to_thread(
                    requests.post,
                    settings.DEEPSEEK_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=90,
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                reply = choice["message"]["content"].strip()
                finish_reason = choice.get("finish_reason")
                logger.info("DeepSeek API call succeeded")
                return reply, finish_reason
            except requests.exceptions.Timeout as exc:
                last_error = exc
                logger.warning("DeepSeek API timeout (%s/%s)", attempt + 1, max_retries)
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                logger.warning("DeepSeek API connection error (%s/%s)", attempt + 1, max_retries)
            except requests.exceptions.HTTPError as exc:
                # 4xx 客户端错误（如 401 密钥无效）不应重试，5xx 可重试。
                last_error = exc
                status = exc.response.status_code if exc.response is not None else 0
                if 400 <= status < 500 and status != 429:
                    logger.error("DeepSeek API HTTP %s — 不可重试，中止", status)
                    break
                logger.warning("DeepSeek API HTTP %s (%s/%s)", status, attempt + 1, max_retries)
            except (ValueError, KeyError, TypeError) as exc:
                # JSON 解析或响应结构异常 — 重试不会改变结果。
                last_error = exc
                logger.error("DeepSeek API 响应格式异常 — 不可重试，中止: %s", exc)
                break

            if attempt < max_retries - 1:
                # 简单指数退避：1s、2s 后重试。
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError("无法连接到 DeepSeek API，请稍后再试。") from last_error
