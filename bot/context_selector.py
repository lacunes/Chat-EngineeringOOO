"""
context_selector — 动态上下文选择器。

在每次聊天前，根据用户消息、当前场景、在场角色等条件，
从世界观、角色卡、长期记忆、关系、剧情状态等模块中
选择本轮最相关的信息注入 prompt，避免无差别塞入全部内容。

核心原则：
1. 每个模块拥有独立 token 预算
2. 按相关性评分排序，优先注入高分项
3. 去重：同一事实不会从多个源重复注入
4. 记录选择原因供调试
5. 失败时回退到安全基础上下文
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("bot.context")


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class ContextItem:
    """单条可注入上下文的条目。"""
    source: str           # 来源模块：world/character/memory/relationship/story/time
    content: str          # 文本内容
    priority: int = 5     # 优先级 1-10（越高越优先）
    reason: str = ""      # 选中原因
    token_estimate: int = 0  # 估算 token 数
    ref_id: str = ""      # 引用 ID（如记忆 ID）


@dataclass
class Budget:
    """单个模块的 token 预算。"""
    max_tokens: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used)

    def can_fit(self, tokens: int) -> bool:
        return tokens <= self.remaining


@dataclass
class SelectionResult:
    """一次上下文选择的结果。"""
    world_context: list[ContextItem] = field(default_factory=list)
    character_context: list[ContextItem] = field(default_factory=list)
    memory_context: list[ContextItem] = field(default_factory=list)
    relationship_context: list[ContextItem] = field(default_factory=list)
    story_context: list[ContextItem] = field(default_factory=list)
    time_context: list[ContextItem] = field(default_factory=list)
    excluded_items: list[ContextItem] = field(default_factory=list)
    budgets: dict[str, Budget] = field(default_factory=dict)
    total_tokens: int = 0


# ═══════════════════════════════════════════════════════════════
# ContextSelector
# ═══════════════════════════════════════════════════════════════

class ContextSelector:
    """动态上下文选择器。

    使用方式：
        selector = ContextSelector(budgets={...})
        result = selector.select(
            user_text="...",
            world=world_obj,
            memory_store=store,
            ...
        )
        # 将 result 注入 prompt
    """

    # 默认预算（token 估算值，中文 1 字符 ≈ 0.5 token）
    DEFAULT_BUDGETS: dict[str, int] = {
        "world":      800,    # 世界观片段
        "character":  400,    # 角色设定
        "memory":     600,    # 长期记忆
        "relationship": 300,  # 关系状态
        "story":      400,    # 剧情状态
        "time":       150,    # 时间信息（固定，通常不裁剪）
    }

    def __init__(self, budgets: dict[str, int] | None = None):
        self._budgets = budgets or dict(self.DEFAULT_BUDGETS)

    def select(
        self,
        user_text: str,
        world,
        memory_store,           # MemoryStore 实例
        relationship_manager,   # RelationshipManager 实例
        time_manager,           # TimeManager 实例
        story_state,            # StoryStateManager 实例
        active_characters: list[str] | None = None,
        current_scene: str = "",
    ) -> SelectionResult:
        """执行上下文选择，返回 SelectionResult。"""
        result = SelectionResult()
        result.budgets = {k: Budget(v) for k, v in self._budgets.items()}

        # ── 时间信息：始终注入（预算充足，不变）──
        time_text = time_manager.get_summary()
        if time_text:
            item = ContextItem(
                source="time",
                content=time_text,
                priority=10,
                reason="当前时间状态（始终注入）",
                token_estimate=_estimate_tokens(time_text),
            )
            result.time_context.append(item)
            result.total_tokens += item.token_estimate

        # ── 世界观：选择相关片段 ──
        world_items = self._select_world_context(world, user_text, current_scene, result.budgets["world"])
        result.world_context = world_items
        result.total_tokens += sum(it.token_estimate for it in world_items)

        # ── 角色设定：选择在场/被提及的角色 ──
        char_items = self._select_character_context(world, user_text, active_characters or [], result.budgets["character"])
        result.character_context = char_items
        result.total_tokens += sum(it.token_estimate for it in char_items)

        # ── 长期记忆：按相关性选择 ──
        mem_items, mem_excluded = self._select_memory_context(
            memory_store, user_text, active_characters or [], result.budgets["memory"]
        )
        result.memory_context = mem_items
        result.excluded_items.extend(mem_excluded)
        result.total_tokens += sum(it.token_estimate for it in mem_items)

        # ── 关系状态 ──
        rel_items = self._select_relationship_context(
            relationship_manager, active_characters or [], result.budgets["relationship"]
        )
        result.relationship_context = rel_items
        result.total_tokens += sum(it.token_estimate for it in rel_items)

        # ── 剧情状态 ──
        story_text = story_state.get_summary()
        if story_text:
            item = ContextItem(
                source="story",
                content=story_text,
                priority=8,
                reason="当前剧情状态",
                token_estimate=_estimate_tokens(story_text),
            )
            budget = result.budgets["story"]
            if budget.can_fit(item.token_estimate):
                result.story_context.append(item)
                budget.used += item.token_estimate
                result.total_tokens += item.token_estimate
            else:
                result.excluded_items.append(item)

        return result

    # ══════════════════════════════════════════════════════════
    # 模块选择逻辑
    # ══════════════════════════════════════════════════════════

    def _select_world_context(self, world, user_text: str, scene: str, budget: Budget) -> list[ContextItem]:
        """从世界观中选择相关片段。默认只注入 SYSTEM_PROMPT（由外部处理），此处可补充场景描述。"""
        items = []
        system_prompt = getattr(world, 'SYSTEM_PROMPT', '')
        if system_prompt:
            # 世界观 SYSTEM_PROMPT 在外部作为固定层注入，此处不重复
            pass

        # 如果有 START_SCENE 且匹配当前场景，补充
        start_scene = getattr(world, 'START_SCENE', '')
        if start_scene and scene and _keyword_match(scene, start_scene):
            item = ContextItem(
                source="world",
                content=f"[场景描述]\n{start_scene[:500]}",
                priority=7,
                reason=f"开场场景匹配当前场景: {scene[:30]}",
                token_estimate=_estimate_tokens(start_scene[:500]),
            )
            if budget.can_fit(item.token_estimate):
                items.append(item)
                budget.used += item.token_estimate

        return items

    def _select_character_context(
        self, world, user_text: str, active_characters: list[str], budget: Budget
    ) -> list[ContextItem]:
        """选择角色设定：优先在场角色和被用户提及的角色。"""
        items = []
        characters = getattr(world, 'CHARACTERS', {}) or {}
        if not characters:
            return items

        # 评分角色相关性
        scored = []
        for name, info in characters.items():
            if not isinstance(info, dict):
                continue
            score = 3  # 基础分
            # 在场角色优先
            if name in active_characters:
                score += 4
            # 用户提及的角色
            if name in user_text:
                score += 3
            scored.append((score, name, info))

        scored.sort(key=lambda x: x[0], reverse=True)

        for score, name, info in scored:
            text = f"{name}: {info.get('description', info.get('personality', ''))}"[:200]
            if not text.strip():
                continue
            item = ContextItem(
                source="character",
                content=text,
                priority=min(10, score),
                reason=f"角色分数={score}（在场={name in active_characters}, 提及={name in user_text}）",
                token_estimate=_estimate_tokens(text),
            )
            if budget.can_fit(item.token_estimate):
                items.append(item)
                budget.used += item.token_estimate
            if budget.remaining <= 0:
                break

        return items

    def _select_memory_context(
        self, store, user_text: str, active_characters: list[str], budget: Budget
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """从长期记忆中按相关性评分选择。返回 (selected, excluded)。"""
        all_items = store.active_items()
        if not all_items:
            return [], []

        # 相关性评分
        scored = []
        for mem in all_items:
            score = _score_memory_relevance(mem, user_text, active_characters)
            scored.append((score, mem))

        # 按评分排序（高分优先）
        scored.sort(key=lambda x: x[0], reverse=True)

        selected = []
        excluded = []
        seen_content: set[str] = set()

        for score, mem in scored:
            text = f"[{mem.type}] {mem.content}"
            if mem.participants:
                text += f"（{'、'.join(mem.participants)}）"
            tokens = _estimate_tokens(text)

            item = ContextItem(
                source="memory",
                content=text,
                priority=min(10, max(1, int(score * 10))),
                reason=_describe_memory_score(score, mem, user_text, active_characters),
                token_estimate=tokens,
                ref_id=mem.id,
            )

            # 去重检查
            content_key = mem.content.strip()[:50]
            if content_key in seen_content:
                excluded.append(item)
                continue
            seen_content.add(content_key)

            if budget.can_fit(tokens):
                selected.append(item)
                budget.used += tokens
                store.record_recall(mem.id)
            else:
                excluded.append(item)

        return selected, excluded

    def _select_relationship_context(
        self, rel_manager, active_characters: list[str], budget: Budget
    ) -> list[ContextItem]:
        """选择关系状态：优先涉及在场角色的关系。"""
        summary = rel_manager.get_summary()
        if not summary:
            return []

        # 如果摘要不太长，直接使用
        tokens = _estimate_tokens(summary)
        if budget.can_fit(tokens):
            item = ContextItem(
                source="relationship",
                content=summary,
                priority=8,
                reason="当前角色关系",
                token_estimate=tokens,
            )
            budget.used += tokens
            return [item]
        return []


# ═══════════════════════════════════════════════════════════════
# 相关性评分
# ═══════════════════════════════════════════════════════════════

def _score_memory_relevance(mem, user_text: str, active_characters: list[str]) -> float:
    """对一条记忆与当前上下文的相关性评分（0.0~1.0）。

    评分因素：
    - 记忆类型权重
    - 参与者匹配（在场角色/用户提及）
    - 内容关键词匹配
    - 重要性基础分
    - 最近召回次数加分
    - 已解决承诺降权
    """
    score = 0.0

    # 1. 重要性基础分 (0~0.3)
    score += mem.importance * 0.3

    # 2. 类型权重
    type_weights = {
        "promise": 0.25,      # 活跃承诺最重要
        "event": 0.20,        # 剧情事件
        "relationship": 0.18, # 关系变化
        "goal": 0.18,         # 目标
        "fact": 0.12,         # 稳定事实
        "scene_state": 0.15,  # 场景状态
        "secret": 0.10,       # 秘密（仅相关时高）
        "preference": 0.08,   # 偏好
    }
    score += type_weights.get(mem.type, 0.10)

    # 3. 参与者匹配 (0~0.25)
    if mem.participants:
        matched = sum(1 for p in mem.participants if p in active_characters or p in user_text)
        score += min(0.25, matched * 0.12)

    # 4. 内容关键词匹配 (0~0.15)
    content_lower = mem.content.lower()
    text_lower = user_text.lower()
    if any(kw in content_lower for kw in text_lower.split() if len(kw) >= 2):
        score += 0.15

    # 5. 最近召回加分 (0~0.05)
    if mem.recall_count > 0:
        score += min(0.05, mem.recall_count * 0.01)

    # 6. 已解决承诺降权
    if mem.status == "resolved":
        score *= 0.5

    return min(1.0, score)


def _describe_memory_score(score: float, mem, user_text: str, active_characters: list[str]) -> str:
    """生成可读的召回原因。"""
    reasons = []
    if mem.importance >= 0.8:
        reasons.append("高重要度")
    if mem.participants:
        matched = [p for p in mem.participants if p in active_characters or p in user_text]
        if matched:
            reasons.append(f"涉及{'、'.join(matched)}")
    if mem.recall_count > 0:
        reasons.append(f"已召回{mem.recall_count}次")
    if mem.type == "promise":
        reasons.append("活跃承诺")
    return f"评分={score:.2f} " + ", ".join(reasons) if reasons else f"评分={score:.2f}"


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """估算中文文本的 token 数（中文约 1 字符 = 0.5 token，英文约 1 词 = 1.3 token）。"""
    if not text:
        return 0
    # 简单估算：中文按字符/1.5，英文按词*1.3
    import re
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    return int(chinese_chars * 0.5 + english_words * 1.3 + 10)  # +10 余量


def _keyword_match(text: str, target: str) -> bool:
    """检查 text 中是否有 target 的关键词。"""
    if not text or not target:
        return False
    # 提取 target 中的关键词（2字以上中文词）
    import re
    words = re.findall(r'[\u4e00-\u9fff]{2,}', target)
    return any(w in text for w in words)
