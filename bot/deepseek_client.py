"""
DeepSeekClient 兼容层。

本项目原有代码通过 DeepSeekClient 调用 LLM API。
现在底层已切换到 LLMRouter（多供应商路由），
但 DeepSeekClient 保留原有类名和接口，内部转发到 LLMRouter。

这样做的好处：
- 旧代码（telegram_handlers / memory_manager / relationship_manager / time_manager）
  几乎不需要改动，只需要通过 DeepSeekClient 间接使用 LLMRouter。
- Web 模板中 `ctx.client.model_name` 仍然可用。
"""

import logging

from bot.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class DeepSeekClient:
    """LLM 调用兼容层。

    保留原有构造函数签名 (api_key, model_name)，
    但实际调用通过 LLMRouter 走多供应商路由。

    如果未设置 LLMRouter，会使用 router 属性注入（见 main.py）。
    """

    def __init__(self, api_key: str = "", model_name: str = ""):
        """初始化兼容层。

        Args:
            api_key: 保留参数，实际 API Key 从 .env 对应变量读取。
            model_name: 保留参数，仅作为 Router 未就绪时的兜底显示。
        """
        self.api_key = api_key  # 保留兼容
        self._fallback_model_name = model_name  # 兜底显示名
        self._router: LLMRouter | None = None

    @property
    def model_name(self) -> str:
        """动态读取当前实际使用的模型名（从 Router 最后一次成功调用获取）。

        如果 Router 未就绪或无调用记录，回退到构造时传入的兜底名。
        """
        if self._router:
            status = self._router.get_dashboard_status()
            current = status.get("current_model")
            if current:
                return current
        return self._fallback_model_name

    def set_router(self, router: LLMRouter) -> None:
        """注入 LLMRouter 实例（由 main.py 在初始化时调用）。"""
        self._router = router

    @property
    def router(self) -> LLMRouter | None:
        """获取底层路由器（供外部访问 provider 状态）。"""
        return self._router

    async def chat(
        self,
        messages: list,
        max_tokens: int = None,
        temperature: float = 0.85,
        purpose: str = "main_chat",
    ) -> tuple[str, str]:
        """调用 LLM API（签名与旧版完全兼容）。

        内部转发到 LLMRouter.chat()，由路由器根据 task_type
        自动选择最佳 provider、处理 fallback 和冷却。

        Args:
            messages: OpenAI 格式的消息列表
            max_tokens: 最大生成 token 数
            temperature: 生成温度
            purpose: 调用用途标签

        Returns:
            (reply_text, finish_reason)
        """
        if self._router is None:
            raise RuntimeError("LLMRouter 未初始化，请检查 main.py 配置。")

        # 通过路由器调用
        return await self._router.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            purpose=purpose,
        )
