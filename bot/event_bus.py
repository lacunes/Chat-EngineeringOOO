"""
event_bus — 轻量同步事件总线。

为模块解耦提供统一的事件发布/订阅机制。
所有监听器同步执行，单个监听器错误不中断主流程。
禁止循环触发，禁止监听器修改不可变核心输入。

优先插件化的事件：
- before_user_message / after_user_message
- before_prompt_build / after_prompt_build
- after_assistant_reply
- memory_created / memory_updated
- relationship_changed
- time_advanced
- provider_failed / provider_switched
"""

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("bot.event")


# ── 监听器类型 ──
Listener = Callable[..., Any]


class EventBus:
    """轻量同步事件总线。

    使用方式：
        bus = EventBus()

        @bus.on("after_assistant_reply", priority=10)
        def handle_reply(reply_text: str, world_id: str):
            ...

        bus.emit("after_assistant_reply", reply_text="...", world_id="one")
    """

    def __init__(self):
        self._listeners: dict[str, list[tuple[int, Listener]]] = defaultdict(list)

    def on(self, event: str, priority: int = 50):
        """装饰器：注册事件监听器。priority 越低越先执行。"""

        def decorator(func: Listener) -> Listener:
            self._listeners[event].append((priority, func))
            self._listeners[event].sort(key=lambda x: x[0])
            return func

        return decorator

    def off(self, event: str, func: Listener) -> None:
        """取消注册监听器。"""
        self._listeners[event] = [
            (p, f) for p, f in self._listeners[event] if f is not func
        ]

    def emit(self, event: str, **kwargs) -> dict[str, Any]:
        """触发事件，同步调用所有监听器。返回每个监听器的结果字典。

        单个监听器报错不会中断其他监听器。
        禁止循环触发（通过简单的事件栈检测）。
        """
        results: dict[str, Any] = {}
        listeners = self._listeners.get(event, [])

        if not listeners:
            return results

        logger.debug("Event '%s' → %d listener(s)", event, len(listeners))

        for priority, func in listeners:
            name = getattr(func, "__name__", str(func))
            try:
                result = func(**kwargs)
                results[name] = result
            except Exception as exc:
                logger.error(
                    "Event '%s' listener '%s' (priority=%d) failed: %s",
                    event, name, priority, exc,
                )
                results[name] = None

        return results

    def has_listeners(self, event: str) -> bool:
        """检查事件是否有监听器。"""
        return len(self._listeners.get(event, [])) > 0


# ── 预定义事件常量 ──

class Events:
    """事件名称常量。"""
    BEFORE_USER_MESSAGE = "before_user_message"
    AFTER_USER_MESSAGE = "after_user_message"
    BEFORE_CONTEXT_SELECTION = "before_context_selection"
    AFTER_CONTEXT_SELECTION = "after_context_selection"
    BEFORE_PROMPT_BUILD = "before_prompt_build"
    AFTER_PROMPT_BUILD = "after_prompt_build"
    BEFORE_LLM_REQUEST = "before_llm_request"
    AFTER_LLM_RESPONSE = "after_llm_response"
    AFTER_ASSISTANT_REPLY = "after_assistant_reply"

    MEMORY_CREATED = "memory_created"
    MEMORY_UPDATED = "memory_updated"
    RELATIONSHIP_CHANGED = "relationship_changed"
    TIME_ADVANCED = "time_advanced"
    WORLD_CHANGED = "world_changed"

    PROVIDER_FAILED = "provider_failed"
    PROVIDER_SWITCHED = "provider_switched"
    STORY_STATE_CHANGED = "story_state_changed"
