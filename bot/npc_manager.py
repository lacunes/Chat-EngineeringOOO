"""
NPC主动行为管理器。

职责：
1. 加载当前世界文件中定义的NPC配置
2. 根据概率权重和冷却系统决定哪些NPC应当主动行动
3. 生成"舞台指令"注入到系统提示词，由主模型在回复中自然融入
4. 追踪每个NPC的冷却状态，防止过度活跃
5. 支持消息驱动和定时驱动两种触发模式（定时器集成在 tick() 中，无需额外线程）

设计理念：
- 零额外API调用 —— NPC行为由主模型在一次回复中同时生成
- 舞台指令只提供方向和约束，不给具体对白（把创作空间留给模型）
- 冷却系统 + 概率 + 关键词感知三重控制，确保NPC不会喧宾夺主
- 如果世界文件未定义NPCS，管理器静默不工作（向后兼容）
"""

import logging
import random
import time

from config import settings

logger = logging.getLogger(__name__)


class NPCManager:
    """NPC主动行为管理器。

    每个世界文件可通过 NPCS 字典定义自己的NPC阵容。
    管理器在每次用户消息后评估触发条件，生成舞台指令给主模型。

    触发机制：
    - 消息驱动：用户发消息时，基于NPC权重 × 全局概率进行随机判定
    - 定时驱动：集成在 tick() 中，距离上次定时检查超过 NPC_TIMER_INTERVAL 秒时触发
    - 上下文感知：用户消息提到NPC名字时，该NPC触发概率翻倍
    - 冷却系统：触发后需经过 cooldown_messages 条消息才能再次触发
    """

    def __init__(self, world, memory):
        """
        Args:
            world: 世界模块（来自 data/worlds/*.yaml），应包含可选的 NPCS 字典
            memory: MemoryManager 实例，用于读取当前剧情上下文
        """
        self.world = world
        self.memory = memory

        # npcs: 归一化后的NPC配置 {npc_id: {name, description, personality, ...}}
        # cooldowns: 每个NPC的剩余冷却消息数 {npc_id: int}
        # total_messages: 自Bot启动以来的总消息计数
        # _pending_directions: 定时器触发的待发送舞台指令列表
        # _last_timer_check: 上次定时器检查的时间戳（秒）
        self.npcs: dict[str, dict] = {}
        self.cooldowns: dict[str, int] = {}
        self.total_messages: int = 0
        self._pending_directions: list[str] = []
        self._last_timer_check: float = time.time()

        self._load_npcs()

        if self.npcs:
            logger.info(
                "NPC Manager loaded %d NPC(s) for world '%s': %s",
                len(self.npcs),
                getattr(self.world, 'WORLD_NAME', 'unknown'),
                ', '.join(npc['name'] for npc in self.npcs.values()),
            )
        else:
            logger.info(
                "NPC Manager: world '%s' has no NPCs defined — NPC system disabled",
                getattr(self.world, 'WORLD_NAME', 'unknown'),
            )

    # ── 公开接口 ─────────────────────────────────────────────

    def tick(self) -> None:
        """每收到一条用户消息时调用。

        执行两个操作：
        1. 更新全局消息计数和所有NPC的冷却计时器
        2. 检查定时器是否到期，若到期则触发 timer-based 评估
           —— 这样定时器复用消息驱动的事件循环，无需独立后台线程

        定时器阈值 = NPC_TIMER_INTERVAL 秒（默认300秒 = 5分钟）。
        设得太短（<60秒）会频繁评估浪费CPU；设得太长（>900秒=15分钟）
        会让NPC在用户长时间沉默后也迟迟不行动。
        """
        self.total_messages += 1

        # 递减冷却计时器：每条消息将冷却倒数减1
        for npc_id in list(self.cooldowns):
            if self.cooldowns[npc_id] > 0:
                self.cooldowns[npc_id] -= 1

        # 定时器检查：集成在主消息循环中，避免 asyncio 事件循环兼容问题
        now = time.time()
        if now - self._last_timer_check >= settings.NPC_TIMER_INTERVAL:
            self._last_timer_check = now
            self._check_timer_triggers()

    def get_stage_directions(self, user_text: str = '', forbidden_events: list[str] | None = None) -> str:
        """评估并返回应注入系统提示词的舞台指令。

        这是NPC系统的核心入口。流程：
        1. 遍历NPC检查消息触发条件（冷却 + 权重概率 + 关键词感知）
        2. 合并 _pending_directions 中定时器触发的待发送指令
        3. 过滤与 forbidden_events 冲突的指令
        4. 生成舞台指令文本并设置冷却
        5. 返回格式化的舞台指令字符串（无触发则返回空字符串）

        Args:
            user_text: 用户最新消息文本，用于关键词感知提升相关NPC触发概率
            forbidden_events: 当前禁止的事件关键词列表（来自剧情状态）

        Returns:
            舞台指令字符串，如果无NPC触发则返回空字符串。
        """
        if not self.npcs and not self._pending_directions:
            return ''

        # 消息驱动触发（带关键词感知）
        triggered = self._evaluate_triggers(context_text=user_text)

        directions: list[str] = []

        # 先取出定时器触发的待发送指令（timeline 先于 message）
        if self._pending_directions:
            directions.extend(self._pending_directions)
            self._pending_directions.clear()

        # 再加入消息驱动的触发
        for npc_id in triggered:
            npc = self.npcs[npc_id]
            directions.append(self._build_direction(npc))
            self.cooldowns[npc_id] = npc['cooldown_messages']
            logger.debug(
                "NPC '%s' triggered by message (cooldown: %d messages)",
                npc['name'],
                npc['cooldown_messages'],
            )

        if not directions:
            return ''

        # ── 根据 forbidden_events 过滤 ──
        if forbidden_events:
            filtered = []
            filtered_out = []
            for d in directions:
                text_lower = d.lower()
                blocked = False
                for pattern in forbidden_events:
                    if pattern.strip().lower() in text_lower:
                        blocked = True
                        filtered_out.append(pattern)
                        break
                if blocked:
                    logger.debug("NPC direction filtered by forbidden_event: %s", filtered_out[-1])
                else:
                    filtered.append(d)
            if filtered_out and not filtered:
                logger.info("All NPC directions blocked by forbidden_events: %s", filtered_out)
                return ''
            directions = filtered

        # 构建完整的舞台指令块
        header = (
            "\n[舞台指令]\n"
            "以下NPC在当前场景中应主动行动。"
            "请将他们的行为自然地融入你的叙事回复中，不要以列表或单独段落的方式罗列NPC行为，"
            "而是作为故事叙述的一部分来描写（如：'正在这时，旅馆老板端着一壶热茶走了过来...'）。\n"
        )
        return header + '\n'.join(f"- {d}" for d in directions)

    def get_status_text(self) -> str:
        """返回NPC系统状态摘要，供 /status 命令使用。"""
        if not self.npcs:
            return "NPC系统：未启用（当前世界未定义NPC）"

        elapsed = time.time() - self._last_timer_check
        next_timer = max(0, settings.NPC_TIMER_INTERVAL - int(elapsed))

        lines = [
            f"NPC系统：已启用（{len(self.npcs)} 个NPC）",
            f"总消息数：{self.total_messages}",
            f"距下次定时检查：约 {next_timer} 秒",
        ]
        for npc_id, npc in self.npcs.items():
            cd = self.cooldowns.get(npc_id, 0)
            cd_text = f"冷却中（剩余 {cd} 条消息）" if cd > 0 else "就绪"
            lines.append(f"  - {npc['name']}：{cd_text}（权重 {npc['activation_weight']:.0%}）")
        return '\n'.join(lines)

    # ── 内部方法 ─────────────────────────────────────────────

    def _load_npcs(self) -> None:
        """从世界文件中加载NPC配置，填充默认值。

        世界文件中的 NPCS 是一个字典，键为NPC内部ID，值为配置字典。
        此方法对每个NPC进行归一化处理，确保所有字段都存在默认值，
        避免后续代码因缺少字段而抛出 KeyError。
        """
        raw_npcs = getattr(self.world, 'NPCS', None)
        if not raw_npcs or not isinstance(raw_npcs, dict):
            return

        for npc_id, cfg in raw_npcs.items():
            if not isinstance(cfg, dict):
                logger.warning("NPC '%s' 配置格式错误（非字典），跳过", npc_id)
                continue

            weight = float(cfg.get('activation_weight', 0.2))
            self.npcs[npc_id] = {
                'name': str(cfg.get('name', npc_id)),
                'description': str(cfg.get('description', '')),
                'personality': str(cfg.get('personality', '')),
                'goals': list(cfg.get('goals', [])),
                'typical_actions': list(cfg.get('typical_actions', [])),
                'activation_weight': max(0.0, min(1.0, weight)),
                # 冷却最少5条消息，防止同一个NPC在连续对话中频繁出现
                'cooldown_messages': max(5, int(cfg.get('cooldown_messages', 15))),
            }
            self.cooldowns[npc_id] = 0

    @staticmethod
    def _build_direction(npc: dict) -> str:
        """根据NPC配置构建单条舞台指令。

        抽取出来避免在 check_timer_triggers 和 get_stage_directions 中
        重复相同的字符串拼接逻辑。

        Args:
            npc: 归一化后的NPC配置字典

        Returns:
            格式化的舞台指令单行文本
        """
        action_hints = (
            '、'.join(npc['typical_actions'][:3])
            if npc['typical_actions']
            else '做出符合其性格的举动'
        )
        return (
            f"【{npc['name']}】{npc['description']}。"
            f"性格：{npc['personality']}。"
            f"当前可采取的行动方向：{action_hints}。"
        )

    def _check_timer_triggers(self) -> None:
        """定时器驱动的NPC触发检查。

        由 tick() 在定时器到期时调用，不直接暴露给外部。
        生成的舞台指令存入 _pending_directions，等到下次用户消息时一起注入。
        使用 TIMER_ACTIVATION_MULTIPLIER (0.6) 降低概率，避免NPC在用户沉默时过度活跃。
        """
        if not self.npcs:
            return

        triggered = self._evaluate_triggers(
            timer_multiplier=settings.NPC_TIMER_ACTIVATION_MULTIPLIER,
        )
        if not triggered:
            return

        for npc_id in triggered:
            npc = self.npcs[npc_id]
            self._pending_directions.append(self._build_direction(npc))
            self.cooldowns[npc_id] = npc['cooldown_messages']
            logger.info(
                "Timer triggered NPC '%s' — queued for next user message",
                npc['name'],
            )

    def _evaluate_triggers(
        self,
        timer_multiplier: float = 1.0,
        context_text: str = '',
    ) -> list[str]:
        """评估哪些NPC应当被触发。

        三层过滤：
        1. 冷却检查 —— 冷却中的NPC直接跳过
        2. 关键词感知 —— 用户消息中提到NPC名字 → 该NPC概率 × CONTEXT_BOOST_MULTIPLIER
        3. 概率判定 —— NPC权重 × 全局基础概率 × 模式系数 × 上下文系数

        概率公式：
          实际触发概率 = activation_weight × NPC_BASE_ACTIVATION × timer_multiplier × context_boost

        例如：
          NPC权重0.3 × 全局0.5 × 消息模式1.0 × 关键词命中2.0 = 30% 概率
          NPC权重0.3 × 全局0.5 × 定时模式0.6 × 无关键词1.0 = 9% 概率

        Args:
            timer_multiplier: 定时器模式系数（<1.0 降低概率）
            context_text: 用户消息文本，用于关键词感知

        Returns:
            应触发的NPC ID列表（已按权重排序，最多 NPC_MAX_ACTIONS_PER_CHECK 个）
        """
        candidates: list[tuple[str, float]] = []

        for npc_id, npc in self.npcs.items():
            # 第一层：冷却检查 —— 冷却中的NPC不会行动
            if self.cooldowns.get(npc_id, 0) > 0:
                continue

            # 第二层：关键词感知 —— 用户提到NPC名字则概率翻倍
            context_boost = 1.0
            if context_text and npc['name'] in context_text:
                context_boost = settings.NPC_CONTEXT_BOOST_MULTIPLIER
                logger.debug(
                    "NPC '%s' name found in user message — probability boosted ×%.1f",
                    npc['name'],
                    context_boost,
                )

            # 第三层：概率判定
            effective_prob = (
                npc['activation_weight']
                * settings.NPC_BASE_ACTIVATION
                * timer_multiplier
                * context_boost
            )
            if random.random() < effective_prob:
                candidates.append((npc_id, npc['activation_weight']))

        if not candidates:
            return []

        # 按权重降序排列，优先触发高权重NPC
        candidates.sort(key=lambda x: x[1], reverse=True)

        # 限制每次最多触发的NPC数量
        # 设为1：一次最多一个NPC行动，叙事清晰
        # 设为2+：可能出现多NPC同时行动，增加混乱但也增加活力
        max_events = settings.NPC_MAX_ACTIONS_PER_CHECK
        if len(candidates) > max_events:
            # 从前 max_events×2 个候选中以权重为倾向随机采样
            # 保留一定随机性，不是每次都是权重最高的NPC触发
            pool_size = min(len(candidates), max_events * 2)
            pool = candidates[:pool_size]
            selected = random.sample(pool, min(max_events, len(pool)))
            selected.sort(key=lambda x: x[1], reverse=True)
            candidates = selected
        else:
            candidates = candidates[:max_events]

        return [npc_id for npc_id, _ in candidates]
