# Prompt 结构说明

## 当前结构（四层）

```
┌─────────────────────────────────────────────────────┐
│ msg 0: [固定层]                                     │  ← LLM API prefix cache 从此处开始匹配
│   world.SYSTEM_PROMPT                               │
│   + TIME_INJECT_INSTRUCTION                         │
│   + RELATION_INJECT_INSTRUCTION                     │
│   （世界不变则永远不变，100% cache 命中）             │
├─────────────────────────────────────────────────────┤
│ msg 1: [半固定层]                                   │
│   ContextSelector 输出（State→Relationship→Fact）：  │
│   ├─ State:  场景状态 + 剧情状态（始终注入）         │
│   ├─ Rel:    关系数值（按相关角色注入）              │
│   └─ Fact:   长期事实/事件/偏好（相关性评分竞争）     │
│   （State 几乎不变，Rel 偶尔变，Fact 精炼/新增时变） │
├─────────────────────────────────────────────────────┤
│ msg 2: [动态层]                                     │
│   NPC 舞台指令                                      │
│   + runtime_directive                               │
│   （每轮可能变化）                                   │
├─────────────────────────────────────────────────────┤
│ msg 3+: [对话层]                                    │
│   最近 N 条聊天记录                                 │
├─────────────────────────────────────────────────────┤
│ [当前输入]                                          │
│   用户消息（在最末条消息中）                         │
└─────────────────────────────────────────────────────┘
```

## 为什么固定内容放最前面

大多数 LLM API（DeepSeek、智谱等）的 prefix cache 从前缀开始匹配。如果 prompt 开头是固定不变的内容，那么这段内容的 KV cache 可以被完全复用，节省推理时间和费用。

- `world.SYSTEM_PROMPT` 在同一个世界中永远不变
- `TIME_INJECT_INSTRUCTION` 是纯指令模板，不含具体时间值
- `RELATION_INJECT_INSTRUCTION` 是纯指令模板，不含具体关系数值
- 该指令明确把关系值定义为内部导演数据，禁止正文引用总值、变化量或面板式关系数字
- 这三段合在一起 → **100% cache 命中**

## 半固定层的内容选择（Phase 4：State → Relationship → Fact）

msg 1（半固定层）由 `ContextSelector.build_prompt_context()` 统一构建，按以下优先级：

1. **State Layer**（始终注入，不参与评分竞争）
   - 当前场景状态（MemoryStore 中唯一的 active `scene_state`）
   - 当前剧情状态（StoryStateManager）
   - 当前时间（TimeManager，已在 msg 0 注入指令，此处注入具体数值）

2. **Relationship Layer**（按相关角色注入）
   - 关系网络数值（RelationshipManager 6 维度）
   - 只注入涉及在场/被提及角色的关系
   - 摘要来自当前共享 RelationshipManager；其状态写入后与 JSON 自动校验

3. **Fact Layer**（按相关性评分竞争，预算控制）
   - 角色设定（在场/被提及优先）
   - 长期记忆（fact/event/preference/secret/goal）
   - 相关性评分因素：类型权重、参与者匹配、关键词匹配、重要性、召回次数

## 为什么动态内容放后面

- NPC 舞台指令：每轮可能触发不同的 NPC
- runtime_directive：随时可能开关

这些内容每轮都可能变化，如果放在固定内容前面，会导致整个 prompt 的 cache 全部失效。

关系、时间、剧情状态、场景状态与相关事实均只从 Selector 输出一次；动态层只保留 NPC 舞台指令和临时导演指令，避免重复注入和预算失效。

## 为什么不能为了缓存删除剧情信息

缓存命中率是成本优化手段，**聊天体验是第一目标**。

- 删除动态信息 = 模型看不到 NPC 行动
- 没有关系状态 → NPC 对话语气失去依据
- 没有 NPC 指令 → 世界失去活力

**宁可 cache miss，不能剧情 miss。**

## 哪些内容属于动态层（不应进入固定层）

- ❌ NPC 舞台指令
- ❌ runtime_directive 导演指令
- ❌ 任何含具体数值的动态信息

## 哪些内容属于固定层（可以进入 msg 0）

- ✅ world.SYSTEM_PROMPT（世界设定、写作规则）
- ✅ TIME_INJECT_INSTRUCTION（时段→描写的映射指令模板）
- ✅ RELATION_INJECT_INSTRUCTION（关系数值→行为倾向的指令模板）
- ✅ NPC_STAGE_DIRECTION_INSTRUCTION（舞台指令→叙事融合的指令模板）

注意：指令模板是固定的，具体数据是动态的。例如 `TIME_INJECT_INSTRUCTION` 说"当看到 [当前时间] 段落时请根据时段调整描写"，这是指令模板；实际的时间数据 "第3天 夏 下午" 是通过 ContextSelector 在半固定层注入的。

## 长期记忆的管理

### 写入
- 长期记忆通过 `MemoryStore` 结构化存储
- `scene_state` 类型采用 **upsert 语义**：写入新状态时自动将旧状态标记为 `superseded`，同一时刻只有 1 条 active scene_state
- `relationship` 类型不再写入 MemoryStore，由 `RelationshipManager` 独立管理

### 注入
- `scene_state` 在 State Layer 中始终注入（优先级 10），不参与评分竞争
- `fact/event/preference/secret/goal` 在 Fact Layer 中按相关性评分竞争，受 budget 控制
- 关系数值通过 RelationshipManager → ContextSelector → msg 1 注入
