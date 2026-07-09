# 项目架构

Telegram AI Roleplay Framework — 基于 Telegram + 多模型供应商（LLM Router）的长期记忆角色扮演框架。

## 整体架构

```
用户 Telegram 消息
    │
    ▼
telegram_handlers.py (RoleplayBot)
    │
    ├─ WorldManager: 检测世界切换 + 热重载 YAML
    ├─ NPC Manager: 检查 NPC 触发条件
    ├─ Time Manager: 检测时间关键词推进
    ├─ ContextSelector: State→Relationship→Fact 三层动态选择
    │   ├─ State:  scene_state + story_state + time（始终注入）
    │   ├─ Rel:   关系网络数值（相关角色注入）
    │   └─ Fact:  fact/event/preference（评分竞争）
    ├─ Memory Manager → MemoryStore: 结构化长期记忆读写
    │   └─ scene_state: upsert 语义（自动 supersede 旧状态）
    ├─ Relationship Manager: 关系状态（唯一权威源，RLock + 回滚）
    ├─ Story State Manager: 剧情状态
    │
    ▼
deepseek_client.py (兼容层) → llm_router.py (多供应商路由)
    │
    ├─ 读取 providers.yaml（支持热重载）
    ├─ 自动 fallback / 冷却 / 额度耗尽检测
    ├─ 支持 auto/manual 模式（Web 面板管理）
    └─ 记录 logs/llm_usage.jsonl
    │
    ▼
回复用户
    │
    ▼
EventBus.emit("after_assistant_reply")
    ├─ [pri=10] 记忆维护（压缩 + 长期记忆提取[过滤relationship] + 关系抽取）
    └─ [pri=20] 时间计数器更新
    │
    └─ RelationshipManager 变更时发射 "relationship_changed"
```

## 模块职责

### `main.py`
入口。初始化 WorldManager → LLMRouter → RoleplayBot，启动 Telegram Bot 和 Web 管理面板（守护线程）。
世界数据通过 WorldManager 加载，支持运行时热切换（无需重启）。
初始化 LLMRouter 并注入到 DeepSeekClient 兼容层。
Telegram 命令菜单通过 `Application.post_init` 中的 `set_my_commands` 非关键初始化；超时或网络异常只记录 warning，不阻断 Web 面板或 polling 启动。
日志系统在 handler 层挂载统一脱敏过滤器，覆盖 root、console 和专题日志 handler，并将 `httpx/httpcore` 常规请求日志压到 WARNING。

### `bot/telegram_handlers.py` — RoleplayBot
- 接收并处理 Telegram 消息和命令
- 调用 ContextSelector 动态选择本轮上下文
- 组装 prompt：固定世界观 → 选择后的长期记忆+角色+关系 → 动态状态 → 短期上下文
- 调度后台任务（记忆压缩、长期记忆提取、关系分析）
- 注入 NPC 舞台指令、时间数据、剧情节奏指令、剧情状态
- 每次聊天前调用 `WorldManager._ensure_world_current()` 检测世界切换
- 职责：**只负责 Telegram 交互流程和 prompt 组装**

### `bot/context_selector.py` — ContextSelector（v3 新增，Phase 4 增强）
- 动态上下文选择器，替代原来的"全量记忆无差别注入"
- 输入：用户消息、世界、角色列表、长期记忆、关系、剧情状态、时间
- **三层优先级**（State → Relationship → Fact）：
  1. **State Layer**：scene_state + 剧情状态 + 时间（始终注入，不参与评分竞争）
  2. **Relationship Layer**：关系网络数值（按在场/被提及角色注入）
  3. **Fact Layer**：fact/event/preference/secret/goal（按相关性评分竞争，预算控制）
- 每个模块拥有独立 token 预算
- 相关性评分因素：记忆类型权重、参与者匹配、关键词匹配、重要性、召回次数
- 去重：同一事实不会从多个源重复注入
- 记录选择原因供调试（Web `/context-debug` 端点）
- 选择失败时自动回退到安全基础上下文

### `bot/memory_store.py` — MemoryStore（v3 新增，Phase 2 增强）
- 结构化长期记忆持久化存储
- `MemoryItem` 数据类：id/world_id/type/participants/importance/confidence/status/tags 等 17 个字段
- 支持 8 种记忆类型：fact/relationship/event/promise/preference/secret/goal/scene_state
  - **scene_state**：upsert 语义 — 写入时自动标记旧状态为 superseded，同一时刻仅 1 条 active
  - **relationship**：已由 RelationshipManager 独立管理，不再写入 MemoryStore
- 支持 6 种生命周期状态：active/resolved/superseded/archived/deleted
- 承诺/誓约管理：promise_from/to/status/linked_event
- 旧格式自动迁移（分类标签前缀的字符串列表 → 结构化记录）
- 内容去重合并（完全相同或高度相似自动合并）
- 加载时自动清理历史重复 scene_state
- 原子写入 + 自动备份 + JSON 损坏保护
- 按类型/参与者/状态/重要性查询

### `bot/event_bus.py` — EventBus（v3，Phase 3 增强）
- 轻量同步事件总线，模块解耦
- 在 `main.py` 创建单例，注入 `RoleplayBot` 和 `RelationshipManager`
- **已接入事件**：
  - `after_assistant_reply`（pri=10 记忆维护 + pri=20 时间更新）
  - `relationship_changed`：关系数值变化时发射（含 changes 和 hints）
- 递归触发检测（`_emitting_stack`）+ 自动请求ID（`uuid.hex[:8]`）
- 单监听器错误不中断其他监听器（递归触发的 RuntimeError 除外）

### `bot/memory_manager.py` — MemoryManager
- 管理短期记忆（`data/sessions/{世界名}_chat.json`）
- 委托 MemoryStore 管理结构化长期记忆（v3）
- `long_memory` 属性保持向后兼容（返回文本列表）
- 自动记忆提取（每 N 轮触发，带本地关键词预检）
  - 提取时过滤 `[relationship]` 条目（由 RelationshipManager 独立管理）
- 短期记忆压缩（超量时生成摘要）
- 长期记忆精炼（AI 去重合并，失败时保留原数据）
- 构建发送给模型的 messages 列表（固定→半固定→动态→对话 四层结构）
- 日志写入 `logs/memory.log`

### `bot/world_manager.py` — WorldManager
- 统一管理世界数据的加载、切换和热重载
- active_world 从 `data/runtime_state.json` 读取（优先于 .env）
- Web 面板切换世界后下次聊天即时生效，无需重启 main.py
- 检测 `data/worlds/<name>.yaml` 文件修改并自动热重载

### `bot/llm_router.py` — LLMRouter
- **多供应商 LLM 路由器**
- 读取 `providers.yaml`，支持**热重载**
- 按 task_type + priority 自动选择最佳 provider
- **故障自动 fallback**：失败 → 重试 → 切换下一个 provider
- **冷却机制**：连续失败 N 次进入冷却
- **额度耗尽永久跳过**
- **模式管理**：auto/manual 模式通过 Web 面板切换
- 记录 `logs/llm_usage.jsonl`
- 持久化状态到 `data/provider_state.json`

### `bot/relationship_manager.py` — RelationshipManager（Phase 1+3 增强）
- 管理角色间结构化关系数据（6 维度），是关系数据的**唯一权威源**
- 自动关系分析（低频触发 + 本地信号预检）
- 110/-100 双端死锁机制
- **RLock 保护**：Web 保存和 AI 抽取全程持锁，消除竞态条件
- **revision 乐观版本检查**：抽取启动时保存版本，应用时版本不同则丢弃 stale 结果
- **回滚保护**：修改前自动创建快照，异常时恢复
- **一致性校验**：每次写入后回读 JSON，验证内存关系、revision 与持久化一致
- **审计元数据**：持久化最后修改来源/时间/变化，逐维度写入结构化 `relation.log`
- **事件发射**：变更后通过 EventBus 发射 `relationship_changed` 事件（`applied_changes` + `revision`）
- Web 面板保存改为增量更新（不清除 AI 发现的角色对）

### `bot/story_state.py` — StoryStateManager
- 管理剧情状态（章节/场景/地点/冲突/目标/节奏等）
- 纯本地 JSON 文件，不调用 API；运行状态统一位于 `data/state/`

### `bot/npc_manager.py` — NPCManager
- 消息驱动 + 定时驱动双重触发机制
- 权重 × 全局概率 × 关键词感知三层判定

### `bot/time_manager.py` — TimeManager
- 管理剧情时间（天数/时段/季节）
- 用户消息关键词检测推进时间

### `bot/safe_io.py`
- 通用原子写入（JSON/YAML/Text）
- 自动备份 + 写入后校验 + 旧备份清理

### `bot/utils.py`
- 世界数据加载、对话格式化、回复分段、敏感信息过滤

### `config/settings.py`
- 所有可调参数从 `.env` 读取，每项带详细中文注释

### `config/prompts.py`
- 所有系统提示词模板

### `web/` — Web 管理面板
- Flask 应用，Session 安全登录 + CSRF 保护
- **v3 设计系统**：浅色/暗色双主题、SVG 图标（Lucide 风格）、统一组件体系
- 概览、配置中心、世界观、模型管理、记忆管理、记忆检查、关系网络、时间、日志
- `/context-debug` 端点查看上下文选择详情
- `/health` 端点提供轻量探活，未登录只返回最小健康信息，登录后返回 polling、世界、Provider 和最近消息时间
- CSP 保持 `script-src 'self'`；页面脚本必须位于 `web/static/js/`，模板不允许 inline `<script>` 或 `onclick/onchange/onsubmit`
- 零外部 CDN，敏感信息自动过滤

### `logs/` — 分层日志
| 文件 | 内容 |
|------|------|
| `app.log` | 全部运行日志 |
| `error.log` | 仅 ERROR+ 级别 |
| `llm_usage.jsonl` | LLM 调用记录（所有 provider） |
| `memory.log` | 记忆提取/压缩/精炼 |
| `relation.log` | 关系抽取版本、stale 丢弃、逐维度 before/delta/after、正文数字警告 |
| `story.log` | 剧情状态加载/更新 |
| `web_audit.log` | Web 面板操作审计 |

### 配置文件
| 文件 | 用途 |
|------|------|
| `.env` | 敏感配置（Token、API Key、用户 ID 等） |
| `providers.yaml` | Provider 配置，支持热重载，自动备份 |
| `data/provider_state.json` | Provider 运行时状态 |
| `data/runtime_state.json` | 运行时状态（active_world） |
| `data/memory/{world}_memories.json` | 结构化长期记忆（v3 新格式） |
| `data/memory/{world}_long_term.json` | 旧格式长期记忆（自动迁移） |
| `data/state/{world}_relationships.json` | 关系网络运行状态 |
| `data/state/{world}_time_state.json` | 时间运行状态 |
| `data/state/{world}_story_state.json` | 剧情运行状态 |

旧 `memory/` 中的关系、时间和剧情 JSON 仅作兼容来源：新文件缺失时会复制有效 JSON 到 `data/state/`，旧文件不会被自动删除。

## 主聊天数据流（v3 + Phase 1-4 增强）

```
用户 Telegram 消息
→ telegram_handlers 接收
→ WorldManager 检测世界切换 / YAML 文件更新（热重载）
→ NPC tick() 更新冷却
→ 获取 NPC 舞台指令（含 forbidden_events 过滤）
→ 用户消息存入短期记忆
→ 时间推进检测（关键词）
→ ContextSelector 动态选择上下文（State→Relationship→Fact 三层）：
    ├─ [State]  scene_state + 剧情状态 + 时间（始终注入，优先级 10）
    ├─ [Rel]   关系网络数值（按在场/被提及角色注入，优先级 8）
    └─ [Fact]  角色设定 + fact/event/preference（相关性评分竞争，预算控制）
→ 构建 prompt（四层结构）：
    1. [固定] world.SYSTEM_PROMPT + TIME_INJECT_INSTRUCTION + RELATION_INJECT_INSTRUCTION
    2. [半固定] ContextSelector 输出（State + Relationship + Fact）
    3. [动态] NPC舞台指令 + 时间数据 + runtime_directive
    4. [对话] 最近 N 条聊天记录
→ DeepSeekClient.chat()（兼容层）
→ LLMRouter.chat()（多供应商路由）
    ├─ 检查 providers.yaml 是否更新（热重载）
    ├─ 按 task_type 筛选可用 provider
    ├─ 跳过 disabled / cooldown / exhausted / 缺 Key 的 provider
    ├─ 按 priority 尝试调用
    ├─ 失败自动 fallback 到下一个
    └─ 记录 logs/llm_usage.jsonl
→ 检测明确的关系面板式数字表达（只记录警告，不改写正文）
→ 原始角色正文写入短期记忆并原样回复用户（不再拼接关系变化提示）
→ EventBus.emit("after_assistant_reply")
    ├─ [pri=10] _schedule_background_maintenance():
    │    记忆压缩 → 长期记忆提取（过滤[relationship]）→ 关系抽取
    │    抽取记录 message range + base revision；应用前版本变化则丢弃
    └─ [pri=20] time_manager.on_assistant_reply(): 时间计数器更新
→ RelationshipManager 变更时发射 "relationship_changed" 事件
```
