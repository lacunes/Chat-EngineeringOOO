import asyncio
import logging
import random
import time
from functools import wraps

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import utils
from bot.npc_manager import NPCManager
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
    世界观来自 worlds/*.py，记忆由 MemoryManager 管理，模型调用由 DeepSeekClient 管理。
    """

    def __init__(self, world, memory, client, relationship_manager):
        self.world = world
        self.memory = memory
        self.client = client
        self.relationship_manager = relationship_manager
        # NPC主动行为管理器 —— 如果世界文件未定义NPC则静默不工作
        self.npc_manager = NPCManager(world, memory)
        # 防止后台记忆维护任务堆积
        self._bg_maintenance_running = False

    def is_authorized(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == settings.ALLOWED_ID)

    async def send_unauthorized(self, update: Update) -> None:
        if update.message:
            await update.message.reply_text("你不是我认识的人。")

    @require_auth
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("User %s started world %s", update.effective_user.id, self.world.WORLD_NAME)
        await update.message.reply_text(self.world.START_SCENE)

    @require_auth
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"当前状态：\n"
            f"当前世界：{self.world.WORLD_NAME}\n"
            f"短期记忆条数：{self.memory.message_count}\n"
            f"长期记忆条数：{self.memory.long_memory_count}\n"
            f"短期记忆文件：memory/{self.world.WORLD_NAME}_memory.json\n"
            f"长期记忆文件：memory/{self.world.WORLD_NAME}_world_memory.json\n"
            f"上下文条数：{settings.CONTEXT_LENGTH}\n"
            f"长期记忆注入条数：{settings.LONG_MEMORY_CONTEXT_LIMIT}\n"
            f"自动长期记忆间隔：{settings.AUTO_MEMORY_INTERVAL} 条消息\n"
            f"模型：{settings.MODEL_NAME}\n"
            f"回复长度：{settings.MIN_REPLY_TOKENS}~{settings.MAX_REPLY_TOKENS}\n"
            f"分段阈值：{settings.SPLIT_THRESHOLD} 字符\n"
            f"续写上限：/c {settings.CONTINUE_LIMIT}\n"
            f"\n{self.npc_manager.get_status_text()}"
        )

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
        self.memory.reset_confirm_users.pop(user_id, None)
        logger.info("User %s reset world %s", user_id, self.world.WORLD_NAME)
        await update.message.reply_text("已确认，当前世界的记忆和关系网络均已清空。")

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
        return reply

    async def continue_story(self) -> str:
        # 续写本质上也是一次模型调用，只是用户输入固定为 CONTINUE_PROMPT。
        return await self.generate_reply(
            user_text=prompts.CONTINUE_PROMPT,
            max_tokens=utils.get_reply_length(),
            length_notice="\n\n（这一段似乎还没说完，可以继续发送 /c。）",
        )

    async def generate_reply(
        self,
        user_text: str,
        length_notice: str,
        max_tokens: int | None = None,
    ) -> str:
        """统一处理普通回复和续写回复的公共流程。

        关键优化：compress_old_memory 和 auto_extract_long_memory
        从主流程中移出，作为后台任务异步执行，不再阻塞用户收到回复。
        """
        # ── NPC主动行为：更新冷却、获取舞台指令 ──
        self.npc_manager.tick()
        stage_directions = self.npc_manager.get_stage_directions(user_text)

        self.memory.add_user_message(user_text)

        # 构建系统提示词：如果有NPC舞台指令，先注入处理指令再附舞台指令
        system_prompt = self.world.SYSTEM_PROMPT
        if stage_directions:
            system_prompt = (
                system_prompt
                + "\n"
                + prompts.NPC_STAGE_DIRECTION_INSTRUCTION
                + "\n"
                + stage_directions
            )

        # ── 注入关系网络摘要 ──
        relation_summary = self.relationship_manager.get_summary()
        if relation_summary:
            system_prompt = system_prompt + relation_summary

        # ── 主 API 调用（关键路径，不阻塞）──
        reply, finish = await self.client.chat(
            self.memory.build_messages(system_prompt),
            max_tokens=max_tokens,
        )
        if finish == "length":
            reply += length_notice

        self.memory.add_assistant_message(reply)
        self.relationship_manager.on_assistant_reply()
        self.memory.save_memory()

        # ── 后台记忆维护 + 关系抽取（不阻塞回复）──
        self._schedule_background_maintenance()

        # ── 上轮关系变化提示（加在回复开头）──
        pending = self.relationship_manager.take_pending_hints()
        if pending:
            reply = "（" + "；".join(pending) + "）\n\n" + reply

        return reply

    def _schedule_background_maintenance(self) -> None:
        """调度后台记忆压缩、长期记忆抽取、关系网络抽取。"""
        if self._bg_maintenance_running:
            return

        async def _run():
            self._bg_maintenance_running = True
            try:
                await self.memory.compress_old_memory(self.client)
                await self.memory.auto_extract_long_memory(self.client)
                await self.relationship_manager.auto_extract(
                    self.memory.memory, self.client,
                )
            except Exception as exc:
                logger.warning("Background maintenance failed: %s", exc)
            finally:
                self._bg_maintenance_running = False

        asyncio.create_task(_run())
