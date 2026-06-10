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
    ├─ Memory Manager: 记忆读写 + prompt 构建
    ├─ Relationship Manager: 关系状态
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
回复用户 → 后台维护（记忆压缩/提取、关系分析）
```

## 模块职责

### `main.py`
入口。初始化 WorldManager → LLMRouter → RoleplayBot，启动 Telegram Bot 和 Web 管理面板（守护线程）。
世界数据通过 WorldManager 加载，支持运行时热切换（无需重启）。
初始化 LLMRouter 并注入到 DeepSeekClient 兼容层。

### `bot/telegram_handlers.py` — RoleplayBot
- 接收并处理 Telegram 消息和命令
- 组装 prompt：固定世界观 → 长期记忆 → 动态状态 → 短期上下文 → 用户输入
- 调度后台任务（记忆压缩、长期记忆提取、关系分析）
- 注入 NPC 舞台指令、关系摘要、时间数据、剧情节奏指令、剧情状态
- 每次聊天前调用 `WorldManager._ensure_world_current()` 检测世界切换
- 职责：**只负责 Telegram 交互流程和 prompt 组装**

### `bot/world_manager.py` — WorldManager（新增）
- 统一管理世界数据的加载、切换和热重载
- active_world 从 `data/runtime_state.json` 读取（优先于 .env）
- Web 面板切换世界后下次聊天即时生效，无需重启 main.py
- 检测 `data/worlds/<name>.yaml` 文件修改并自动热重载
- 不再支持 `worlds/*.py` 旧格式

### `bot/llm_router.py` — LLMRouter
- **多供应商 LLM 路由器**，替代原来直接调用 DeepSeek API
- 读取 `providers.yaml`，支持**热重载**（修改后无需重启）
- 按 task_type + priority 自动选择最佳 provider
- **故障自动 fallback**：失败 → 重试 → 切换下一个 provider
- **冷却机制**：连续失败 N 次进入冷却
- **额度耗尽永久跳过**：quota exhausted 类错误标记为 exhausted
- **模式管理**：auto/manual 模式通过 Web 面板切换（不再使用 Telegram /provider 命令）
- 支持 Web 面板「测试连接」、启用/禁用 provider、清除失败状态
- 记录 `logs/llm_usage.jsonl`（含 max_tokens、temperature、finish_reason 等）
- 持久化状态到 `data/provider_state.json`（启动时自动补齐/清理）
- 最近 20 次调用历史环形缓冲区供 Web 面板展示
- 必要时给管理员发送 Telegram 提醒（fallback/冷却/耗尽/全部失败）

### `bot/deepseek_client.py` — DeepSeekClient（兼容层）
- 保留原有类名和 `chat()` 接口签名
- 内部转发到 `LLMRouter`，不直接调用 API
- 旧代码无需修改即可受益于多供应商路由
- `model_name` 属性自动同步为当前活跃 provider 的模型名

### `bot/memory_manager.py` — MemoryManager
- 管理短期记忆（`memory/{世界名}_memory.json`）
- 管理长期记忆（`memory/{世界名}_world_memory.json`）
- 自动记忆提取（每 N 轮触发，带本地关键词预检）
- 短期记忆压缩（超量时生成摘要）
- 长期记忆精炼（去重合并）
- 构建发送给模型的 messages 列表（固定→半固定→动态→对话 四层结构）
- 日志写入 `logs/memory.log`

### `bot/relationship_manager.py` — RelationshipManager
- 管理角色间结构化关系数据（6 维度：好感/信任/畏惧/依赖/怀疑/敌意）
- 自动关系分析（低频触发 + 本地信号预检，零 API 浪费）
- 110/-100 双端死锁机制
- 生成注入 prompt 的关系摘要
- 关系变化日志写入 `logs/relation.log`

### `bot/story_state.py` — StoryStateManager
- 管理剧情状态（章节/场景/地点/冲突/目标/节奏/允许事件/禁止事件等）
- 纯本地 JSON 文件，不调用 API
- 生成 `[当前剧情状态]` 注入 dynamic_state
- 提供 NPC 事件过滤（forbidden_events）和权重提升（allowed_events）
- 日志写入 `logs/story.log`

### `bot/npc_manager.py` — NPCManager
- 从世界文件加载 NPC 配置
- 消息驱动 + 定时驱动双重触发机制
- 权重 × 全局概率 × 关键词感知三层判定
- 生成舞台指令注入 system prompt
- 支持 forbidden_events 过滤

### `bot/time_manager.py` — TimeManager
- 管理剧情时间（天数/时段/季节）
- 用户消息关键词检测推进时间
- 长场景温和提示
- 每日摘要生成

### `bot/utils.py`
- 世界数据加载（YAML 优先 → JSON 兜底）
- 对话文本格式化
- 回复分段
- 回复长度随机算法

### `config/settings.py`
- 所有可调参数从 `.env` 读取
- 每项带详细中文注释

### `config/prompts.py`
- 所有系统提示词模板
- 记忆提取、精炼、压缩、关系分析、NPC 指令、时间指令等

### `worlds/` ❌ 已删除
- 旧世界格式（`worlds/<name>.py`）已彻底移除，不再使用。
- 世界数据现在存储在 `data/worlds/*.yaml`。
- 切换世界通过 Web 面板世界编辑器即时生效，无需重启。

### `data/worlds/`
- 每个 `*.yaml` 文件定义一个世界（主格式，推荐手动编辑）。
- 旧 `*.json` 文件仍可兼容读取（过渡期）。
- 包含：WORLD_NAME、START_SCENE、SYSTEM_PROMPT、CHARACTERS、RULES、LOCATIONS、EVENT_POOL、NPCS。
- 长文本字段（START_SCENE、SYSTEM_PROMPT）使用 YAML `|` 块文本格式。

### `web/` — Web 管理面板
- Flask 应用，Session 安全登录
- 仪表盘、配置中心、世界编辑器、模型管理、记忆管理、记忆污染检查、关系网络、时间与剧情节奏、日志查看
- 深色主题，移动端适配，零外部 CDN

### `logs/` — 分层日志
| 文件 | 内容 |
|------|------|
| `app.log` | 全部运行日志 |
| `error.log` | 仅 ERROR+ 级别 |
| `api_usage.jsonl` | API 用量 + cache 命中率（旧格式，DeepSeek 专用） |
| `llm_usage.jsonl` | LLM 调用记录（新格式，所有 provider） |
| `memory.log` | 记忆提取/压缩/精炼 |
| `relation.log` | 关系分析触发/变化 |
| `story.log` | 剧情状态加载/更新 |
| `../web_audit.log` | Web 面板操作审计 |

### 配置文件
| 文件 | 用途 |
|------|------|
| `.env` | 敏感配置（Token、API Key、用户 ID 等） |
| `providers.yaml` | **非敏感** provider 配置（模型名、优先级、超时等），支持热重载，自动备份 |
| `data/provider_state.json` | provider 运行时状态（冷却、耗尽、失败计数），启动时自动补齐/清理 |
| `data/runtime_state.json` | 运行时状态（active_world），Web 面板切换世界时写入 |

## 主聊天数据流

```
用户 Telegram 消息
→ telegram_handlers 接收
→ WorldManager 检测世界切换 / YAML 文件更新（热重载）
→ NPC tick() 更新冷却
→ 获取 NPC 舞台指令（含 forbidden_events 过滤）
→ 用户消息存入短期记忆
→ 时间推进检测（关键词）
→ 构建 prompt：
    1. [固定] world.SYSTEM_PROMPT + TIME_INJECT_INSTRUCTION
    2. [半固定] [长期记忆]...
    3. [动态] NPC指令 + 关系摘要 + 时间数据 + 剧情状态 + runtime_directive
    4. [对话] 最近 N 条聊天记录
    5. [当前] 用户输入（已在短期记忆中）
→ DeepSeekClient.chat()（兼容层）
→ LLMRouter.chat()（多供应商路由）
    ├─ 检查 providers.yaml 是否更新（热重载）
    ├─ 按 task_type 筛选可用 provider
    ├─ 跳过 disabled / cooldown / exhausted / 缺 Key 的 provider
    ├─ 按 priority 尝试调用
    ├─ 失败自动 fallback 到下一个
    └─ 记录 logs/llm_usage.jsonl
→ 回复用户
→ 后台维护：记忆压缩 → 长期记忆提取 → 关系分析
```
