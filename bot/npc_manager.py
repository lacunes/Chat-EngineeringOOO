"""
NPC主动行为管理器。

职责：
1. 加载当前世界文件中定义的NPC配置
2. 根据概率权重和冷却系统决定哪些NPC应当主动行动
3. 生成"舞台指令"注入到系统提示词，由主模型在回复中自然融入
4. 追踪每个NPC的冷却状态，防止过度活跃

设计理念：
- 零额外API调用 —— NPC行为由主模型在一次回复中同时生成
- 舞台指令只提供方向和约束，不给具体对白（把创作空间留给模型）
- 冷却系统 + 概率双重控制，确保NPC不会喧宾夺主
- 如果世界文件未定义NPCS，管理器静默不工作（向后兼容）
"""

import logging
import random

from config import settings

logger = logging.getLogger(__name__)


class NPCManager:
    """NPC主动行为管理器。

    每个世界文件可通过 NPCS 字典定义自己的NPC阵容。
    管理器在每次用户消息后评估触发条件，生成舞台指令给主模型。

    使用方式：
        # 在 RoleplayBot 中
        self.npc_manager = NPCManager(world, memory)

        # 在 generate_reply() 中，构建消息前
        stage_directions = self.npc_manager.get_stage_directions(user_text)
        if stage_directions:
            # 将舞台指令附加到 system prompt 末尾
            ...
    """

    def __init__(self, world, memory):
        """
        Args:
            world: 世界模块（来自 worlds/*.py），应包含可选的 NPCS 字典
            memory: MemoryManager 实例，用于读取当前剧情上下文
        """
        self.world = world
        self.memory = memory

        # npcs: 归一化后的NPC配置 {npc_id: {name, description, personality, ...}}
        # cooldowns: 每个NPC的剩余冷却消息数 {npc_id: int}
        # total_messages: 自Bot启动以来的总消息计数
        # _pending_directions: 定时器触发的待发送舞台指令列表
        self.npcs: dict[str, dict] = {}
        self.cooldowns: dict[str, int] = {}
        self.total_messages: int = 0
        self._pending_directions: list[str] = []

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

        更新全局消息计数和所有NPC的冷却计时器。
        """
        self.total_messages += 1
        for npc_id in list(self.cooldowns):
            if self.cooldowns[npc_id] > 0:
                self.cooldowns[npc_id] -= 1

    def check_timer_triggers(self) -> None:
        """后台定时器调用：评估NPC触发条件，生成舞台指令存入待发送队列。

        与 get_stage_directions 的区别：
        - 本方法由后台 asyncio 定时任务调用，不依赖用户消息
        - 生成的舞台指令存入 _pending_directions，等到下次用户消息时一起注入
        - 使用更低的触发概率（乘以 0.6），避免定时器产生过多NPC行为
        """
        if not self.npcs:
            return

        # 定时器触发使用折半概率，比消息驱动更保守
        original = settings.NPC_BASE_ACTIVATION
        # 临时降低全局概率（不修改 settings 对象，只在本次评估中生效）
        # 通过猴子补丁方式传递降低后的概率
        try:
            # 用 attribute 传递临时概率
            self._timer_mode = True
            triggered = self._evaluate_triggers(timer_multiplier=0.6)
        finally:
            self._timer_mode = False

        if not triggered:
            return

        for npc_id in triggered:
            npc = self.npcs[npc_id]
            action_hints = (
                '、'.join(npc['typical_actions'][:3])
                if npc['typical_actions']
                else '做出符合其性格的举动'
            )
            direction = (
                f"【{npc['name']}】{npc['description']}。"
                f"性格：{npc['personality']}。"
                f"当前可采取的行动方向：{action_hints}。"
            )
            self._pending_directions.append(direction)
            self.cooldowns[npc_id] = npc['cooldown_messages']
            logger.info(
                "Timer triggered NPC '%s' (pending for next user message)",
                npc['name'],
            )

    def get_stage_directions(self, user_text: str = '') -> str:
        """评估并返回应注入系统提示词的舞台指令。

        这是NPC系统的核心入口。流程：
        1. 遍历所有NPC，检查消息触发条件（冷却 + 权重概率）
        2. 合并 _pending_directions 中定时器触发的待发送指令
        3. 为触发的NPC生成舞台指令文本
        4. 设置触发NPC的冷却计时器
        5. 返回格式化的舞台指令字符串（无触发则返回空字符串）

        Args:
            user_text: 用户最新消息文本，可用于上下文感知触发（v1暂未使用）

        Returns:
            舞台指令字符串，如果无NPC触发则返回空字符串。
        """
        if not self.npcs and not self._pending_directions:
            return ''

        # 消息驱动触发
        triggered = self._evaluate_triggers()

        directions: list[str] = []

        # 先加入定时器触发的待发送指令
        if self._pending_directions:
            directions.extend(self._pending_directions)
            self._pending_directions.clear()

        # 再加入消息驱动的触发
        for npc_id in triggered:
            npc = self.npcs[npc_id]
            action_hints = (
                '、'.join(npc['typical_actions'][:3])
                if npc['typical_actions']
                else '做出符合其性格的举动'
            )
            direction = (
                f"【{npc['name']}】{npc['description']}。"
                f"性格：{npc['personality']}。"
                f"当前可采取的行动方向：{action_hints}。"
            )
            directions.append(direction)

            # 冷却：触发后需要经过 cooldown_messages 条消息才能再次触发
            self.cooldowns[npc_id] = npc['cooldown_messages']
            logger.debug(
                "NPC '%s' triggered (cooldown set to %d messages)",
                npc['name'],
                npc['cooldown_messages'],
            )

        if not directions:
            return ''

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

        lines = [
            f"NPC系统：已启用（{len(self.npcs)} 个NPC）",
            f"总消息数：{self.total_messages}",
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
        此方法会对每个NPC进行归一化处理，确保所有字段都存在默认值。
        """
        raw_npcs = getattr(self.world, 'NPCS', None)
        if not raw_npcs or not isinstance(raw_npcs, dict):
            return

        for npc_id, cfg in raw_npcs.items():
            if not isinstance(cfg, dict):
                logger.warning("NPC '%s' 配置格式错误，跳过", npc_id)
                continue

            self.npcs[npc_id] = {
                'name': str(cfg.get('name', npc_id)),
                'description': str(cfg.get('description', '')),
                'personality': str(cfg.get('personality', '')),
                'goals': list(cfg.get('goals', [])),
                'typical_actions': list(cfg.get('typical_actions', [])),
                'activation_weight': self._clamp_weight(
                    float(cfg.get('activation_weight', 0.2))
                ),
                'cooldown_messages': max(
                    5, int(cfg.get('cooldown_messages', 15))
                ),
            }
            self.cooldowns[npc_id] = 0

    @staticmethod
    def _clamp_weight(weight: float) -> float:
        """将触发权重限制在 [0.0, 1.0] 区间内。"""
        return max(0.0, min(1.0, weight))

    def _evaluate_triggers(self, timer_multiplier: float = 1.0) -> list[str]:
        """评估哪些NPC应当被触发。

        评估规则（两层过滤）：
        1. 冷却检查 —— 冷却中的NPC直接跳过
        2. 概率判定 —— 基于 NPC 权重 × 全局基础激活概率 × 模式系数
           - 例如 NPC权重0.3 × 全局0.5 × 1.0 = 15% 实际触发概率
           - 定时器模式系数为 0.6，降低后台触发频率
           - 这样可以通过 .env 中的全局参数调节所有NPC的活跃度

        Args:
            timer_multiplier: 定时器模式系数（<1.0 降低概率，1.0 为消息驱动模式）

        Returns:
            应触发的NPC ID列表（已按权重排序，最多 NPC_MAX_ACTIONS_PER_CHECK 个）
        """
        candidates: list[tuple[str, float]] = []

        for npc_id, npc in self.npcs.items():
            # 第一层：冷却检查
            if self.cooldowns.get(npc_id, 0) > 0:
                continue

            # 第二层：概率判定
            effective_prob = (
                npc['activation_weight']
                * settings.NPC_BASE_ACTIVATION
                * timer_multiplier
            )
            if random.random() < effective_prob:
                candidates.append((npc_id, npc['activation_weight']))

        if not candidates:
            return []

        # 按权重降序排列，优先触发高权重NPC
        candidates.sort(key=lambda x: x[1], reverse=True)

        # 限制每次最多触发的NPC数量
        max_events = settings.NPC_MAX_ACTIONS_PER_CHECK
        if len(candidates) > max_events:
            # 对前 max_events*2 个候选进行随机采样（保留一定随机性）
            pool = candidates[: max_events * 2]
            selected = random.sample(pool, min(max_events, len(pool)))
            selected.sort(key=lambda x: x[1], reverse=True)
            candidates = selected
        else:
            candidates = candidates[:max_events]

        return [npc_id for npc_id, _ in candidates]
