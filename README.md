# Telegram AI Roleplay Framework

Telegram Bot + Web 导演台 + Multi-provider LLM Router + 结构化长期记忆 + 动态上下文选择

项目目标：

> 让世界观、角色设定、剧情规则与机器人核心逻辑彻底解耦。
> 修改剧情时只需要编辑世界文件，而不需要修改主程序。

---

# ✨ 特性

* Telegram 私聊角色扮演
* **多模型供应商路由**（LLM Router：自动选择 + fallback + 冷却）
* 单用户授权
* **Web 热切换世界**（无需重启 main.py）
* 短期记忆 + **结构化长期记忆**（v3：类型/参与者/重要性/生命周期）
* 自动记忆提取与压缩
* **动态上下文选择器**（按相关性评分，模块预算控制）
* 历史对话摘要
* 自动续写
* 长回复自动分段
* NPC主动行为系统
* **Web 管理面板**（浅色/暗色双主题，SVG图标系统）
* **严格 CSP Web 交互**（脚本全部本地静态化，无 inline script / inline handler）
* **轻量健康检查**（`/health`，不触发 Telegram 或模型 API）
* 关系网络系统（6 维度）
* 时间流逝系统（用户驱动）
* 剧情节奏控制
* 记忆污染检查
* **轻量 EventBus**（同步事件发布/订阅，模块解耦）
* GitHub 版本管理

---

# 🏗 架构设计

```text
用户 ──→ Telegram ──→ telegram_handlers.py ──→ LLMClient (兼容层) ──→ LLMRouter
 │                         │                         │                │
 │                    npc_manager.py                  │          providers.yaml
 │                    (NPC主动行为)                    │          provider_state.json
 │                         │                         │                │
 │                    ContextSelector ←───────────────┘          多供应商 API
 │                    (State→Rel→Fact 三层)                       (智谱/DeepSeek/
 │                         │                                     OpenRouter/...)
 │                    memory_manager.py
 │                         │
 │                    MemoryStore (v3)
 │                    (结构化长期记忆)
 │                    scene_state: upsert 语义
 │
 ├── Web 管理面板 ──→ WorldManager ──→ data/worlds/*.yaml
 │                         │
 │                    runtime_state.json
 │
 ├── RelationshipManager (关系唯一源, RLock + 回滚)
 │
 └── EventBus (事件总线: after_assistant_reply + relationship_changed)
```

**模块边界：**

```text
LLMRouter           ：只管模型选择、fallback、provider 状态。
WorldManager        ：只管 active_world、世界文件热加载。
MemoryManager       ：短期记忆 + 委托 MemoryStore 管理结构化长期记忆。
MemoryStore         ：长期记忆 CRUD、查询、旧格式迁移、原子持久化。
                      scene_state: upsert 语义；relationship: 不再写入。
ContextSelector     ：动态选择上下文，State→Relationship→Fact 三层优先级。
RelationshipManager ：关系数据唯一权威源，RLock + 回滚保护，
                      Web/AI/注入 全部走同一实例。
EventBus            ：轻量同步事件总线，解耦记忆/关系/时间/NPC 模块。
Web Panel           ：只管展示和调用管理接口。
Telegram Bot        ：只管聊天入口和用户命令。

runtime_state.json  ：只保存运行时选择（active_world）。
provider_state.json ：只保存 provider 失败、冷却、耗尽、最近错误。
data/sessions/*     ：短期聊天上下文。
data/memory/*       ：结构化长期记忆（v3）+ 摘要。
```

---

# 📁 项目结构

```text
project/
├── main.py                     # 入口
│
├── bot/
│   ├── telegram_handlers.py    # Telegram 命令与消息处理
│   ├── deepseek_client.py      # LLMClient 兼容层
│   ├── llm_router.py           # 多供应商路由器
│   ├── memory_manager.py       # 记忆管理（v3：集成 MemoryStore）
│   ├── memory_store.py         # 结构化长期记忆存储（v3 新增）
│   ├── context_selector.py     # 动态上下文选择器（v3 新增）
│   ├── event_bus.py            # 轻量事件总线（v3 新增）
│   ├── world_manager.py        # 世界数据热加载与切换
│   ├── relationship_manager.py # 关系网络
│   ├── time_manager.py         # 时间流逝
│   ├── npc_manager.py          # NPC 主动行为
│   ├── story_state.py          # 剧情状态管理
│   ├── safe_io.py              # 原子写入与备份工具
│   └── utils.py                # 工具函数
│
├── config/
│   ├── settings.py             # 运行时配置（从 .env 读取）
│   └── prompts.py              # 系统提示词模板
│
├── web/
│   ├── app.py                  # Flask 工厂 + 安全配置
│   ├── routes/
│   │   ├── auth.py             # 登录/登出
│   │   ├── dashboard.py        # 仪表盘 + 上下文调试端点
│   │   ├── config_center.py    # 配置中心
│   │   ├── worlds.py           # 世界编辑器
│   │   ├── memory.py           # 记忆管理
│   │   ├── memory_audit.py     # 记忆污染检查
│   │   ├── relations.py        # 关系网络
│   │   ├── time_routes.py      # 时间与节奏
│   │   ├── providers.py        # 模型供应商管理
│   │   └── logs.py             # 日志查看
│   ├── templates/              # Jinja2 模板（含 _macros.html 组件库）
│   └── static/
│       ├── css/app.css         # 设计系统（浅色/暗色双主题）
│       ├── js/app.js
│       ├── js/providers.js     # 模型管理页交互
│       └── icons/icons.svg     # SVG 图标 Sprite（36个Lucide图标）
│
├── deploy/
│   └── chat-engineering.service # systemd unit 示例（通过 .env 注入环境变量）
├── scripts/
│   └── health_watch.sh          # 本地健康诊断脚本，不自动杀进程
├── data/
│   ├── worlds/                 # 世界数据（YAML 格式）
│   ├── sessions/               # 短期记忆（{world}_chat.json）
│   ├── memory/                 # 长期记忆（{world}_memories.json）+ 摘要
│   ├── runtime_state.json      # 运行时状态
│   └── provider_state.json     # 模型供应商运行时状态
│
├── providers.yaml              # 多模型供应商配置
├── backups/                    # 自动备份目录
├── logs/                       # 日志目录
├── docs/                       # 开发文档
├── requirements.txt
├── .env.example
└── README.md
```

---

# 🧠 记忆系统（v3 结构化 + Phase 1-4 增强）

## 数据结构

v3 起长期记忆使用结构化 `MemoryItem` 记录，替代原来的扁平字符串列表：

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识（mem_xxxxxxxx） |
| `world_id` | 所属世界 |
| `type` | 类型（fact/event/promise/preference/secret/goal/scene_state） |
| `content` | 记忆文本 |
| `participants` | 相关角色列表 |
| `importance` | 重要度（0.0~1.0） |
| `confidence` | 置信度（0.0~1.0） |
| `status` | 生命周期（active/resolved/superseded/archived/deleted） |
| `tags` | 标签列表 |
| `recall_count` | 召回次数 |
| `created_at` / `updated_at` | 时间戳 |
| `promise_from` / `promise_to` / `promise_status` | 承诺管理 |

> **注意**：`relationship` 类型已由 `RelationshipManager` 独立管理，不再写入 MemoryStore。
> `scene_state` 采用 upsert 语义 — 写入时自动将旧状态标记为 `superseded`，同一时刻仅 1 条 active。

## 文件结构

```text
data/sessions/{world}_chat.json        — 短期聊天上下文
data/memory/{world}_memories.json      — 结构化长期记忆（v3 新格式）
data/memory/{world}_long_term.json     — 旧格式（首次加载时自动迁移）
data/memory/{world}_summary.json       — 压缩摘要历史
```

## 旧格式兼容

旧格式（`[hard_fact] ...` 分类标签前缀的字符串列表）在 MemoryStore 首次加载时**自动迁移**为结构化记录。迁移前会创建备份。

## 数据安全

- 所有记忆文件使用**原子写入**（tmp + flush + fsync + os.replace）
- 写入前**自动备份**到 `backups/` 目录
- JSON 损坏时不覆盖原文件，创建损坏备份
- 内容去重：相同/高度相似的内容自动合并
- 切换世界、切换模型、重启 main.py **不清空任何记忆**

---

# 🎯 动态上下文选择（v3 + Phase 4 三层优先级）

`ContextSelector` 在每次聊天前按 **State → Relationship → Fact** 三层优先级动态选择注入 prompt 的内容。

**输入：** 用户消息、世界、角色、长期记忆、关系、剧情状态、时间

**三层优先级：**
| 层级 | 内容 | 策略 |
|------|------|------|
| **State** | scene_state + 剧情状态 + 时间 | 始终注入，优先级 10，不参与竞争 |
| **Relationship** | 关系网络 6 维度数值 | 按在场/被提及角色注入，优先级 8 |
| **Fact** | fact/event/preference/secret/goal | 相关性评分竞争，预算控制 |

**预算控制（默认）：**
| 模块 | Token 预算 |
|------|-----------|
| 世界观 | 800 |
| 角色设定 | 400 |
| 长期记忆 | 600（State 优先消费） |
| 关系状态 | 300 |
| 剧情状态 | 400 |
| 时间信息 | 150 |

选择失败时自动回退到安全的基础上下文（旧方式）。选择结果可在 Web 面板 `/context-debug` 端点查看。

---

# 📡 轻量 EventBus（v3 + Phase 3 增强）

`EventBus` 提供同步事件发布/订阅机制，用于模块解耦。

**已接入事件：** `after_assistant_reply`（记忆维护 + 时间更新）、`relationship_changed`（关系数值变更通知）

预定义事件：`before_user_message`、`after_assistant_reply`、`memory_created`、`relationship_changed`、`time_advanced`、`provider_failed`、`provider_switched` 等 16 个。

特性：优先级控制、单监听器错误隔离、禁止循环触发。

---

# 🌐 Web 管理面板（v3 设计系统）

Web 面板定位为 **"角色扮演导演台"**。

## 设计系统

- **配色**：中性灰白 + 青色 `#0F766E` 强调色
- **主题**：浅色/暗色双模式，localStorage 持久化
- **图标**：36 个 Lucide 风格 SVG 图标（`static/icons/icons.svg` sprite）
- **组件**：统一的 Button / Card / Input / Badge / Table / Stats 组件
- **布局**：左侧固定导航 + 主工作区 + 响应式移动端

## 功能页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 概览 | `/` | 运行状态、记忆/关系/时间统计、NPC、异常 |
| 配置中心 | `/config` | 剧情与行为参数管理 |
| 世界观 | `/worlds` | 热切换、新建/编辑/复制/删除 |
| 模型管理 | `/providers` | Provider 状态、模式切换、测试连接、调用历史 |
| 记忆管理 | `/memory` | 短期/长期记忆浏览、增删改、精炼 |
| 记忆检查 | `/memory-audit` | 规则 + AI 两层污染检查 |
| 关系网络 | `/relations` | 6 维度关系编辑 |
| 时间 | `/time` | 时间状态、快速推进、剧情节奏 |
| 日志 | `/logs` | 实时日志查看 |

## 健康检查

`/health` 用于本地或反代探活，不会请求 Telegram 或任何模型 API。

- 未登录：只返回 `ok`、`process_alive`、`web_alive`、`uptime_seconds`
- 已登录：额外返回 `telegram_polling_started`、`active_world`、`current_provider`、最近消息时间和连续 Telegram 网络错误计数

`scripts/health_watch.sh` 可在 VPS 上每分钟记录一次本机 Web 健康、`main.py` 进程和 `api.telegram.org` 可达性。它只做诊断，不会自动重启或杀进程。

---

# ⚙ 环境变量

核心配置：

| 变量 | 说明 |
|------|------|
| BOT_TOKEN | Telegram Bot Token |
| ALLOWED_ID | 允许使用 Bot 的 Telegram 用户 ID |
| ACTIVE_WORLD | 默认世界 |
| WEB_PORT | Web 面板端口（默认 8080） |
| WEB_HOST | Web 监听地址 |
| WEB_PASSWORD | Web 面板登录密码 |

完整配置项见 `config/settings.py` 注释。

---

# 🗺 Roadmap

* [x] Telegram Bot
* [x] 多世界系统 + 热切换
* [x] 短期记忆 + 结构化长期记忆（v3 MemoryStore）
* [x] 动态上下文选择器（v3 ContextSelector）
* [x] 自动记忆抽取与压缩
* [x] 多模型供应商路由（LLM Router）
* [x] NPC 主动行为系统
* [x] 关系网络系统（6 维度）
* [x] Web 管理面板（v3 设计系统）
* [x] 配置中心
* [x] 记忆污染检查 + 自动清洗
* [x] 剧情节奏控制
* [x] 时间流逝系统
* [x] 轻量 EventBus
* [x] 原子写入 + 自动备份
* [x] 敏感信息统一过滤
* [x] 浅色/暗色双主题
* [x] 严格 CSP 下的 Web 交互
* [x] 非关键 Telegram 命令菜单初始化
* [x] `/health` 与 systemd/健康诊断样例
* [ ] 多角色同时对话
* [ ] 自动事件系统

---

# 📜 License

个人研究使用。
