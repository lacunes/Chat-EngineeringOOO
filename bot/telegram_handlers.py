import asyncio
import json
import logging
import random
import time
from functools import wraps
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import utils
from bot.context_selector import ContextSelector
from bot.memory_manager import MemoryManager
from bot.npc_manager import NPCManager
from bot.relationship_manager import RelationshipManager
from bot.story_state import StoryStateManager
from bot.time_manager import TimeManager
from config import prompts, settings


logger = logging.getLogger(__name__)


def require_auth(func):
    """装饰器：自动检查用户授权，未授权则回复提示并跳过执行。"""
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not self.is_authorized(update):
            await self.send_unauthorized(update)
            return
        return await func(self, update, context, *args, **kwargs)
    return wrapper


class RoleplayBot:
    """Telegram 命令与普通消息处理。

    这个类只负责 Telegram 交互流程。
    世界观来自 data/worlds/*.yaml，记忆由 MemoryManager 管理，模型调用由 DeepSeekClient 管理。
    支持运行时热切换世界（通过 WorldManager）。
    """

    def __init__(self, world_manager, client, event_bus=None):
        self.world_manager = world_manager
        self.client = client
        self.event_bus = event_bus
        self._init_managers()

    def _init_managers(self) -> None:
        """初始化/重新初始化所有世界相关的管理器。"""
        world = self.world_manager.get_world()
        self.world = world
        self.memory = MemoryManager(world.WORLD_NAME)
        self.relationship_manager = RelationshipManager(world.WORLD_NAME)
        self.time_manager = TimeManager(world.WORLD_NAME)
        # NPC主动行为管理器 —— 如果世界文件未定义NPC则静默不工作
        self.npc_manager = NPCManager(world, self.memory)
        # 剧情状态管理器 —— 纯本地 JSON，不影响 API 调用频率
        self.story_state = StoryStateManager(world.WORLD_NAME, settings.MEMORY_DIR)
        # 上下文选择器（v3：动态选择注入内容）
        self.context_selector = ContextSelector()
        # 防止后台记忆维护任务堆积
        self._bg_maintenance_running = False
        self._last_maintenance_time: float = 0.0

        # ── 注册 EventBus 监听器 ──
        self._register_event_listeners()

    def _register_event_listeners(self) -> None:
        """注册 EventBus 监听器（在 _init_managers 中调用，世界切换时重新注册）。"""
        if not self.event_bus:
            return

        # 先移除旧监听器（世界切换时重新注册）
        for name in ("_on_memory_maintenance", "_on_time_update"):
            old = getattr(self, name, None)
            if old is not None:
                self.event_bus.off("after_assistant_reply", old)

        @self.event_bus.on("after_assistant_reply", priority=10)
        def _on_memory_maintenance(**kwargs):
            """回复后触发：记忆压缩 + 长期记忆提取 + 关系抽取。"""
            self._schedule_background_maintenance()

        @self.event_bus.on("after_assistant_reply", priority=20)
        def _on_time_update(**kwargs):
            """回复后触发：更新时间计数器。"""
            self.time_manager.on_assistant_reply(self.memory.message_count)

        # 保存函数引用到实例，供下次 world switch 时 off() 使用
        self._on_memory_maintenance = _on_memory_maintenance  # type: ignore[assignment]
        self._on_time_update = _on_time_update  # type: ignore[assignment]

    def _ensure_world_current(self) -> None:
        """检查世界是否被 Web 面板切换，是则自动重新初始化所有管理器。"""
        current_name = self.world_manager.world_name
        if current_name != self.world.WORLD_NAME:
            logger.info("World changed from %s to %s, reinitializing managers...",
                       self.world.WORLD_NAME, current_name)
            self.reload_world_managers()

    def reload_world_managers(self) -> None:
        """重新加载当前 active_world 并刷新所有世界相关管理器。

        Web 切换世界后应立即调用此方法，使 Telegram Bot 和 Web 面板
        立即同步到新世界，无需等下一轮聊天。
        """
        # 强制 WorldManager 重载当前世界
        self.world_manager.reload_world()
        logger.info("reload_world_managers: reinitializing all managers for world '%s'",
                   self.world_manager.world_name)
        self._init_managers()

    def is_authorized(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == settings.ALLOWED_ID)

    async def send_unauthorized(self, update: Update) -> None:
        if update.message:
            await update.message.reply_text("你不是我认识的人。")

    @require_auth
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("User %s started world %s", update.effective_user.id, self.world.WORLD_NAME)
        scene = self.world.START_SCENE
        await update.message.reply_text(scene)

        # 将开场文本作为 assistant 消息写入短期记忆（避免重复写入）
        last = self.memory.last_assistant_message()
        if last != scene:
            self.memory.add_assistant_message(scene)
            self.memory.save_memory()
            logger.debug("START_SCENE written to short-term memory")
        else:
            logger.debug("START_SCENE already in memory, skipped")

    @require_auth
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # ── Router 状态 ──
        router_lines = []
        if self.client.router:
            state = self.client.router._state
            mode = state.get("mode", "auto")
            manual = state.get("manual_provider")
            router_lines.append(f"模式：{'🔧 手动' if mode == 'manual' else '🔄 自动'}")
            if manual:
                router_lines.append(f"手动优先：{manual}")

            # 最近调用历史
            history = self.client.router.get_call_history()
            last_success = None
            for call in reversed(history):
                if call.get("success"):
                    last_success = call
                    break
            if last_success:
                router_lines.append(
                    f"最近成功：{last_success.get('provider','?')} / {last_success.get('model','?')}"
                    f" ({last_success.get('latency_ms',0)}ms, finish={last_success.get('finish_reason','?')})"
                )

            # Fallback 信息
            last_ft = state.get("last_fallback_time")
            last_fr = state.get("last_fallback_reason")
            if last_ft:
                router_lines.append(f"最近 fallback：{last_ft} — {last_fr}")

            # 问题 provider
            providers = self.client.router.get_provider_list()
            for p in providers:
                issues = []
                if p.get("exhausted"):
                    issues.append(f"🚫{p.get('last_error_type','exhausted')}")
                elif p.get("cooldown_remaining", 0) > 0:
                    issues.append(f"⏳冷却{p['cooldown_remaining']}s")
                if issues:
                    router_lines.append(f"  {p['name']}：{' '.join(issues)}")
        else:
            router_lines.append("路由器未初始化")

        # ── 记忆状态 ──
        mem_status = self.memory.get_memory_status()
        memory_info = []
        memory_info.append(f"文件：{mem_status['chat'].get('path', '?')}")
        memory_info.append(f"对话：{mem_status['chat']['count']} 条")
        memory_info.append(f"长期：{self.memory.long_memory_count} 条（真实记忆）")
        memory_info.append(f"保存：{mem_status['last_save_time']} {'✅' if mem_status['last_save_ok'] else '❌'}")
        if mem_status['was_recovered']:
            memory_info.append("⚠️曾从旧路径恢复")

        # ── 候选 provider 列表 ──
        candidates_str = ""
        if self.client.router:
            candidates = self.client.router._select_candidates("chat")
            if candidates:
                candidates_str = f"候选：{' → '.join(candidates[:5])}"

        lines = [
            f"📊 状态",
            f"世界：{self.world.WORLD_NAME}",
            f"",
            f"🔌 Router：",
        ] + [f"  {l}" for l in router_lines] + [
            f"",
            f"🧠 记忆：",
        ] + [f"  {l}" for l in memory_info] + [
            f"  Token：{settings.MIN_REPLY_TOKENS}~{settings.MAX_REPLY_TOKENS} / 分段>{settings.SPLIT_THRESHOLD}",
            f"  上下文：{settings.CONTEXT_LENGTH}条 / 长期注入{settings.LONG_MEMORY_CONTEXT_LIMIT}条",
            f"  续写上限：/c {settings.CONTINUE_LIMIT}",
        ]
        if candidates_str:
            lines.append(f"")
            lines.append(f"📋 {candidates_str}")
        lines += [
            f"",
            f"{self.npc_manager.get_status_text()}",
        ]

        await update.message.reply_text("\n".join(lines))

    @require_auth
    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # /reset 有二次确认，避免误触后直接清空当前世界记忆。
        user_id = update.effective_user.id
        now = time.time()
        last_request = self.memory.reset_confirm_users.get(user_id)

        if not last_request or now - last_request > settings.RESET_CONFIRM_SECONDS:
            self.memory.reset_confirm_users[user_id] = now
            await update.message.reply_text(
                f"即将重置当前世界记忆。\n"
                f"请在 {settings.RESET_CONFIRM_SECONDS} 秒内再次发送 /reset 确认。"
            )
            return

        self.memory.reset()
        self.relationship_manager.reset()
        self.time_manager.reset()
        self.memory.reset_confirm_users.pop(user_id, None)
        logger.info("User %s reset world %s", user_id, self.world.WORLD_NAME)
        await update.message.reply_text("已确认，当前世界的记忆、关系网络和时间均已重置。")

    @require_auth
    async def cmd_memo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = " ".join(context.args).strip()
        if not text:
            await update.message.reply_text("格式：\n/memo 内容")
            return
        if len(text) > settings.MEMO_SIZE_LIMIT:
            await update.message.reply_text(f"内容过长，限制 {settings.MEMO_SIZE_LIMIT} 字。")
            return

        self.memory.add_long_memory_item(text)
        await self.memory.refine_long_memory(self.client, force=False)
        self.memory.save_long_memory()
        await update.message.reply_text("已写入当前世界的长期记忆。")

    @require_auth
    async def cmd_refine_memo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.memory.refine_long_memory(self.client, force=True)
            await update.message.reply_text(f"长期记忆已精炼，目前 {self.memory.long_memory_count} 条。")
        except Exception as exc:
            logger.error("refinememo error: %s", exc, exc_info=True)
            await update.message.reply_text("长期记忆精炼失败，稍后再试。")

    @require_auth
    async def cmd_relations(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """显示当前世界角色关系摘要。"""
        await update.message.reply_text(
            self.relationship_manager.get_status_text()
        )

    @require_auth
    async def cmd_relation_full(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """显示完整关系网络。"""
        text = self.relationship_manager.get_full_text()
        for part in utils.split_reply(text):
            await update.message.reply_text(part)

    @require_auth
    async def cmd_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """显示当前时间状态。"""
        await update.message.reply_text(self.time_manager.get_status_text())

    @require_auth
    async def cmd_next_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """手动推进一个时段。"""
        new_period = self.time_manager.advance_period()
        await update.message.reply_text(
            f"⏭ 时间推进 → 第{self.time_manager.day}天 · {self.time_manager.season} · {new_period}"
        )

    @require_auth
    async def cmd_next_day(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """推进到第二天清晨并生成昨日摘要。"""
        self.time_manager.advance_day()
        await update.message.reply_text(
            f"📅 推进到第{self.time_manager.day}天清晨，正在生成昨日摘要…"
        )
        await self.time_manager.generate_day_summary(self.memory.memory, self.client)
        await update.message.reply_text(self.time_manager.get_status_text())

    @require_auth
    async def handle_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action=ChatAction.TYPING,
            )
            reply = await self.ask(update.message.text)
            for part in utils.split_reply(reply):
                await update.message.reply_text(part)
            logger.info("Replied to user message")
        except Exception as exc:
            logger.error("chat error: %s", exc, exc_info=True)
            await update.message.reply_text("……信号断了一下。\n\n等一下再试。")

    @require_auth
    async def cmd_continue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        count = 1
        if context.args:
            try:
                count = int(context.args[0])
            except ValueError:
                count = 1
        count = max(1, min(count, settings.CONTINUE_LIMIT))

        try:
            for index in range(count):
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id,
                    action=ChatAction.TYPING,
                )
                reply = await self.continue_story()
                for part in utils.split_reply(reply):
                    await update.message.reply_text(part)
                if index < count - 1:
                    await asyncio.sleep(random.uniform(3, 8))
            logger.info("Continued story %s times", count)
        except Exception as exc:
            logger.error("continue error: %s", exc, exc_info=True)
            await update.message.reply_text("……续不上了，等一下再试。")

    async def ask(self, user_text: str) -> str:
        # 普通聊天主流程：
        # 记录用户消息 -> 压缩检查 -> 调模型 -> 记录回复 -> 自动维护记忆。
        reply = await self.generate_reply(
            user_text=user_text,
            length_notice="\n\n（这一段似乎还没说完，可以发送 /c 继续。）",
        )

        # 每隔 MEMO_REMINDER_INTERVAL 条消息提示一次用户手动记录长期记忆
        if self.memory.message_count % settings.MEMO_REMINDER_INTERVAL == 0:
            reply += (
                "\n\n【记忆提醒】\n"
                "最近剧情可能出现重要关系变化。\n"
                "如有需要可使用：\n"
                "/memo ..."
            )

        # 长场景温和提示
        hint = self.time_manager.get_long_scene_hint()
        if hint:
            reply += hint

        return reply

    async def continue_story(self) -> str:
        # 续写不推进时间、不触发后台维护。
        return await self.generate_reply(
            user_text=prompts.CONTINUE_PROMPT,
            max_tokens=utils.get_reply_length(),
            length_notice="\n\n（这一段似乎还没说完，可以继续发送 /c。）",
            skip_time_advance=True,
            skip_background_maintenance=True,
        )

    async def generate_reply(
        self,
        user_text: str,
        length_notice: str,
        max_tokens: int | None = None,
        skip_time_advance: bool = False,
        skip_background_maintenance: bool = False,
    ) -> str:
        """统一处理普通回复和续写回复的公共流程。

        关键优化：compress_old_memory 和 auto_extract_long_memory
        从主流程中移出，作为后台任务异步执行，不再阻塞用户收到回复。
        """
        # ── 检测世界是否被 Web 面板切换 ──
        self._ensure_world_current()

        # ── NPC主动行为：更新冷却、获取舞台指令 ──
        self.npc_manager.tick()
        # 获取剧情状态的禁止事件列表
        forbidden = self.story_state.state.get("forbidden_events", [])
        stage_directions = self.npc_manager.get_stage_directions(
            user_text, forbidden_events=forbidden,
        )

        self.memory.add_user_message(user_text)

        # ── 用户驱动时间推进（关键词检测；/c 续写跳过）──
        if not skip_time_advance:
            advance = self.time_manager.detect_advance(
                user_text, self.time_manager.time_period,
            )
            if advance:
                action = advance["action"]
                logger.info("Time advance by user message: %s", advance["reason"])
                if action == "advance_day":
                    self.time_manager.advance_day()
                elif action == "advance_period":
                    self.time_manager.advance_period()
                elif action == "jump_to" and advance.get("target"):
                    self.time_manager.jump_to(advance["target"])
                self.time_manager.mark_period_start(self.memory.message_count)

        # ── 构建 Prompt（固定→半固定→动态→对话，最大化 prefix cache 命中）──

        # 1. 固定层：世界设定 + 时间指令（世界不变则永远不变，100% 缓存命中）
        world_prompt = self.world.SYSTEM_PROMPT + "\n" + prompts.TIME_INJECT_INSTRUCTION

        # 2. 半固定层：上下文选择器（动态选择长期记忆、关系、角色等）
        #    提取活跃角色和场景
        active_chars = self.story_state.state.get("active_characters", [])
        current_scene = self.story_state.state.get("scene", "")

        try:
            selection = self.context_selector.select(
                user_text=user_text,
                world=self.world,
                memory_store=self.memory._store,
                relationship_manager=self.relationship_manager,
                time_manager=self.time_manager,
                story_state=self.story_state,
                active_characters=active_chars,
                current_scene=current_scene,
            )
            logger.debug(
                "Context selection: %d items, %d tokens (mem=%d, rel=%d, char=%d)",
                len(selection.memory_context) + len(selection.relationship_context) + len(selection.character_context),
                selection.total_tokens,
                len(selection.memory_context),
                len(selection.relationship_context),
                len(selection.character_context),
            )
            # 存储最近一次选择结果供Web调试
            try:
                from web.app import AppContext
                # 通过 flask current_app 获取 ctx（如果可用）
                import flask
                if flask.has_app_context():
                    ctx = flask.current_app.config.get("ctx")
                    if ctx:
                        ctx.last_selection = _serialize_selection(selection)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Context selector failed, using fallback: %s", exc)
            selection = None

        # 构建半固定层：从选择结果中提取
        long_term_parts: list[str] = []
        if selection:
            # 角色设定
            char_texts = [it.content for it in selection.character_context]
            if char_texts:
                long_term_parts.append("[相关角色]\n" + "\n".join(char_texts))
            # 长期记忆
            mem_texts = [it.content for it in selection.memory_context]
            if mem_texts:
                long_term_parts.append("[长期记忆]\n" + "\n".join(mem_texts))
            # 关系
            rel_texts = [it.content for it in selection.relationship_context]
            if rel_texts:
                long_term_parts.append("\n".join(rel_texts))
        else:
            # 安全回退：使用旧方式的纯文本列表（取最重要的记忆）
            if self.memory.long_memory:
                recent = self.memory.long_memory[:settings.LONG_MEMORY_CONTEXT_LIMIT]
                long_term_parts.append("[长期记忆]\n" + "\n".join(recent))

        long_term_text = "\n".join(long_term_parts) if long_term_parts else None

        # 3. 动态层：当前状态（NPC 指令、时间数据、导演指令）
        dynamic_parts: list[str] = []
        if stage_directions:
            dynamic_parts.append(prompts.NPC_STAGE_DIRECTION_INSTRUCTION + "\n" + stage_directions)

        # 关系摘要（已移到半固定层，动态层不再重复）
        # relation_summary 已通过 context_selector 注入

        time_summary = self.time_manager.get_summary()
        dynamic_parts.append(time_summary)

        # ── 注入剧情状态 ──
        story_summary = self.story_state.get_summary()
        if story_summary:
            dynamic_parts.append(story_summary)
            logger.debug("Story state injected into dynamic_state")

        directive = _load_runtime_directive()
        if directive.get("enabled"):
            directive_prompt = _build_directive_prompt(directive)
            if directive_prompt:
                dynamic_parts.append(directive_prompt)

        dynamic_state = "\n".join(dynamic_parts) if dynamic_parts else None

        # ── 主 API 调用（关键路径，不阻塞）──
        messages = self.memory.build_messages(
            world_prompt=world_prompt,
            long_term_context=long_term_text,
            dynamic_state=dynamic_state,
        )
        reply, finish = await self.client.chat(
            messages,
            max_tokens=max_tokens,
            purpose="continue" if skip_time_advance else "main_chat",
        )
        if finish == "length":
            reply += length_notice

        self.memory.add_assistant_message(reply)
        self.relationship_manager.on_assistant_reply()
        self.memory.save_memory()

        # ── 通过 EventBus 触发后处理（记忆维护 + 时间更新）──
        if not skip_background_maintenance and self.event_bus:
            self.event_bus.emit(
                "after_assistant_reply",
                reply_text=reply,
                memory_snapshot=list(self.memory.memory),
            )

        # ── 上轮关系变化提示（加在回复开头）──
        pending = self.relationship_manager.take_pending_hints()
        if pending:
            reply = "（" + "；".join(pending) + "）\n\n" + reply

        return reply

    def _schedule_background_maintenance(self) -> None:
        """调度后台记忆压缩、长期记忆抽取、关系网络抽取。"""
        if self._bg_maintenance_running:
            return
        now = time.time()
        if now - self._last_maintenance_time < settings.BACKGROUND_MAINTENANCE_COOLDOWN_SECONDS:
            return

        async def _run():
            self._bg_maintenance_running = True
            try:
                # ── 快照：复制当前记忆，避免并发读写冲突 ──
                memory_snapshot = list(self.memory.memory)

                did_compress = await self.memory.compress_old_memory(self.client)
                # 压缩已包含摘要+精炼，跳过冗余的长期记忆抽取
                if not did_compress:
                    await self.memory.auto_extract_long_memory(self.client)
                await self.relationship_manager.auto_extract(
                    memory_snapshot, self.client,
                )
            except Exception as exc:
                logger.warning("Background maintenance failed: %s", exc)
            finally:
                self._bg_maintenance_running = False
                self._last_maintenance_time = time.time()

        asyncio.create_task(_run())


# ═══════════════════════════════════════════════════════════
# 运行时导演指令（runtime_directive.json）
# ═══════════════════════════════════════════════════════════

def _load_runtime_directive() -> dict:
    """加载剧情节奏指令文件。"""
    from config import settings
    path = Path(settings.BASE_DIR) / "runtime_directive.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_directive_prompt(directive: dict) -> str:
    """根据指令构建注入到 system prompt 的导演提示。"""
    phase = directive.get("story_phase", "")
    tendency = directive.get("next_tendency", "")
    if not phase and not tendency:
        return ""

    lines = ["\n[导演指令]"]
    if phase:
        lines.append(f"当前剧情阶段：{phase}。请调整叙事节奏和氛围以匹配此阶段。")
    if tendency:
        lines.append(f"下一轮倾向：{tendency}。请在接下来的回复中自然地引导剧情朝此方向发展。")

    lines.append("注意：这是临时导演提示，不要直接对用户说出这些指令，而是在叙事中自然体现。")
    return "\n".join(lines)


def _serialize_selection(selection) -> dict:
    """将 SelectionResult 序列化为可 JSON 化的字典（供 Web 调试面板）。"""
    from bot.context_selector import SelectionResult
    if not isinstance(selection, SelectionResult):
        return {}

    def item_to_dict(it):
        return {
            "source": it.source,
            "content": it.content[:200] + ("…" if len(it.content) > 200 else ""),
            "priority": it.priority,
            "reason": it.reason,
            "tokens": it.token_estimate,
            "ref_id": it.ref_id,
        }

    return {
        "world": [item_to_dict(it) for it in selection.world_context],
        "character": [item_to_dict(it) for it in selection.character_context],
        "memory": [item_to_dict(it) for it in selection.memory_context],
        "relationship": [item_to_dict(it) for it in selection.relationship_context],
        "story": [item_to_dict(it) for it in selection.story_context],
        "time": [item_to_dict(it) for it in selection.time_context],
        "excluded": [item_to_dict(it) for it in selection.excluded_items],
        "budgets": {k: {"max": v.max_tokens, "used": v.used} for k, v in selection.budgets.items()},
        "total_tokens": selection.total_tokens,
    }
