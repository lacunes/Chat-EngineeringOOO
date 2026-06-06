# 项目架构

Telegram AI Roleplay Framework — 基于 Telegram + DeepSeek 的长期记忆角色扮演框架。

## 整体架构

```
用户 Telegram 消息
    │
    ▼
telegram_handlers.py (RoleplayBot)
    │
    ├─ NPC Manager: 检查 NPC 触发条件
    ├─ Time Manager: 检测时间关键词推进
    ├─ Memory Manager: 记忆读写 + prompt 构建
    ├─ Relationship Manager: 关系状态
    ├─ Story State Manager: 剧情状态
    │
    ▼
deepseek_client.py → DeepSeek API
    │
    ▼
回复用户 → 后台维护（记忆压缩/提取、关系分析）
```

## 模块职责

### `main.py`
入口。初始化所有模块，启动 Telegram Bot 和 Web 管理面板（守护线程）。

### `bot/telegram_handlers.py` — RoleplayBot
- 接收并处理 Telegram 消息和命令
- 组装 prompt：固定世界观 → 长期记忆 → 动态状态 → 短期上下文 → 用户输入
- 调度后台任务（记忆压缩、长期记忆提取、关系分析）
- 注入 NPC 舞台指令、关系摘要、时间数据、剧情节奏指令、剧情状态
- 职责：**只负责 Telegram 交互流程和 prompt 组装**

### `bot/deepseek_client.py` — DeepSeekClient
- 封装 DeepSeek API 调用
- 支持自动重试（最多 3 次）
- 记录 API 用量和 prefix cache 命中率到 `logs/api_usage.jsonl`

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
- 世界文件动态加载
- 对话文本格式化
- 回复分段
- 回复长度随机算法

### `config/settings.py`
- 所有可调参数从 `.env` 读取
- 每项带详细中文注释

### `config/prompts.py`
- 所有系统提示词模板
- 记忆提取、精炼、压缩、关系分析、NPC 指令、时间指令等

### `worlds/`
- 每个 `*.py` 文件定义一个世界
- 包含：WORLD_NAME、START_SCENE、SYSTEM_PROMPT、CHARACTERS、RULES、LOCATIONS、EVENT_POOL、NPCS

### `web/` — Web 管理面板
- Flask 应用，Session 安全登录
- 仪表盘、配置中心、世界编辑器、记忆管理、记忆污染检查、关系网络、时间与剧情节奏、日志查看
- 深色主题，移动端适配，零外部 CDN

### `logs/` — 分层日志
| 文件 | 内容 |
|------|------|
| `app.log` | 全部运行日志 |
| `error.log` | 仅 ERROR+ 级别 |
| `api_usage.jsonl` | API 用量 + cache 命中率 |
| `memory.log` | 记忆提取/压缩/精炼 |
| `relation.log` | 关系分析触发/变化 |
| `story.log` | 剧情状态加载/更新 |
| `../web_audit.log` | Web 面板操作审计 |

## 主聊天数据流

```
用户 Telegram 消息
→ telegram_handlers 接收
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
→ DeepSeek API 调用
→ 回复用户
→ 后台维护：记忆压缩 → 长期记忆提取 → 关系分析
```
