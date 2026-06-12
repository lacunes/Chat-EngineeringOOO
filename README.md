# Telegram AI Roleplay Framework

Telegram Bot + Web 控制台 + Multi-provider LLM Router + WorldManager + MemoryManager

项目目标：

> 让世界观、角色设定、剧情规则与机器人核心逻辑彻底解耦。
> 修改剧情时只需要编辑世界文件，而不需要修改主程序。

---

# ✨ 特性

* Telegram 私聊角色扮演
* **多模型供应商路由**（LLM Router：自动选择 + fallback + 冷却）
* 单用户授权
* **Web 热切换世界**（无需重启 main.py）
* 短期记忆 + 长期记忆
* 自动记忆提取与压缩
* 历史对话摘要
* 自动续写
* 长回复自动分段
* NPC主动行为系统
* Web 管理面板（导演台）
* 关系网络系统（6 维度）
* 时间流逝系统（用户驱动）
* 剧情节奏控制
* 记忆污染检查
* GitHub 版本管理

---

# 🏗 架构设计

```text
用户 ──→ Telegram ──→ telegram_handlers.py ──→ LLMClient (兼容层) ──→ LLMRouter
 │                         │                         │                │
 │                    npc_manager.py                  │          providers.yaml
 │                    (NPC主动行为)                    │          provider_state.json
 │                         │                         │                │
 │                    memory_manager.py ←─────────────┘          多供应商 API
 │                         │                                   (智谱/DeepSeek/
 │                    ┌────┴────┐                              OpenRouter/VSLLM/
 │                    │         │                              MiniMax/Kimi...)
 │                 短期记忆  长期记忆
 │
 ├── Web 管理面板 ──→ WorldManager ──→ data/worlds/*.yaml
 │                         │
 │                    runtime_state.json
 │
 └──────────────── 各管理器边界清晰，互不越界
```

**模块边界：**

```text
LLMRouter     ：只管模型选择、fallback、provider 状态。
WorldManager  ：只管 active_world、世界文件热加载。
MemoryManager ：只管短期记忆、长期记忆、摘要和安全持久化。
Web Panel     ：只管展示和调用管理接口。
Telegram Bot  ：只管聊天入口和用户命令。

runtime_state.json  ：只保存运行时选择（active_world, provider_mode, manual_provider）。
provider_state.json ：只保存 provider 失败、冷却、耗尽、最近错误。
memory/session 文件 ：只保存聊天上下文和记忆。
```

---

# 📁 项目结构

```text
project/
├── main.py                     # 入口
│
├── bot/
│   ├── telegram_handlers.py    # Telegram 命令与消息处理
│   ├── deepseek_client.py      # LLMClient 兼容层（保留旧接口，内部转发到 LLMRouter）
│   ├── llm_router.py           # 多供应商路由器
│   ├── memory_manager.py       # 记忆管理
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
│   │   ├── dashboard.py        # 仪表盘
│   │   ├── config_center.py    # 配置中心
│   │   ├── worlds.py           # 世界编辑器
│   │   ├── memory.py           # 记忆管理
│   │   ├── memory_audit.py     # 记忆污染检查
│   │   ├── relations.py        # 关系网络
│   │   ├── time_routes.py      # 时间与节奏
│   │   ├── providers.py        # 模型供应商管理
│   │   └── logs.py             # 日志查看
│   ├── templates/              # Jinja2 模板
│   └── static/
│
├── data/
│   ├── worlds/                 # 世界数据（YAML 格式）
│   ├── sessions/               # 短期记忆（{world}_chat.json）
│   ├── memory/                 # 长期记忆 + 摘要（{world}_long_term.json 等）
│   ├── runtime_state.json      # 运行时状态
│   └── provider_state.json     # 模型供应商运行时状态
│
├── providers.yaml              # 多模型供应商配置
├── backups/                    # 自动备份目录
├── logs/                       # 日志目录
├── requirements.txt
├── .env.example
└── README.md
```

---

# 🔌 多模型供应商系统 (LLM Router)

项目使用 `LLMRouter` 管理多个 LLM 供应商，支持自动选择和故障切换。

## providers.yaml 配置

```yaml
providers:
  - name: zhipu_glm_air
    enabled: true
    priority: 1
    task_types: ["chat", "memory", "summary", "relation", "background"]
    api_key_env: "ZHIPU_API_KEY"
    base_url: "https://open.bigmodel.cn/api/paas/v4"
    model: "glm-4.5-air"
    timeout_chat_seconds: 60
    timeout_background_seconds: 30
    max_retries: 1
    cooldown_seconds: 300
    max_consecutive_failures: 3
    disable_on_quota_exhausted: true
    thinking_enabled: false
```

每个 provider 通过 `api_key_env` 指定环境变量名（在 `.env` 中配置对应的 API Key）。支持任意兼容 OpenAI Chat Completions 格式的 API。

`base_url` 只需填写到基础路径（如 `https://xxx/v1`），代码会自动拼接 `/chat/completions` 和 `/models`。如果用户填写了完整路径也会自动规范化。

## Web Provider 管理页

访问 Web 面板 → 模型管理，可以：
- 查看所有 provider 状态（启用/冷却/耗尽/Key 状态）
- 切换自动/手动模式
- 启用/禁用 provider
- 测试连接（返回成功/失败原因）
- 编辑 provider 配置（表单弹出，支持从 /v1/models 获取模型列表）
- 添加/删除 provider（删除前二次确认）
- 清除失败/冷却/耗尽状态
- 查看 LLM 调用历史
- 所有操作均通过 POST body 传参，支持含 `[]`、`/`、`@` 等特殊字符的 provider 名称和模型名

## 路由模式

- **自动模式**：按 priority 排序，失败自动 fallback
- **手动模式**：优先使用指定 provider，失败后 fallback 到其他

---

# 🌍 世界观系统

所有世界数据存储在 `data/worlds/*.yaml`（YAML 格式，Web 编辑器可直接编辑）。

每个世界包含：WORLD_NAME、SYSTEM_PROMPT、START_SCENE、CHARACTERS、RULES、LOCATIONS、EVENT_POOL、NPCS。

## Web 热切换世界

在 Web 面板 → 世界编辑器 → 点击"切换"，世界立即生效，**无需重启 main.py**。

切换后 Telegram Bot 和 Web 所有页面会立刻同步到新世界。旧世界的记忆、关系、时间数据不会被删除。

---

# 🧠 记忆系统

## 文件结构

每个世界拥有独立的记忆文件：

```text
data/sessions/{world}_chat.json       — 短期聊天上下文
data/memory/{world}_long_term.json    — 长期记忆
data/memory/{world}_summary.json      — 压缩摘要历史
```

旧路径（`memory/{world}_memory.json`）在启动时自动迁移到新路径。

## 数据安全

- 所有记忆文件使用**原子写入**（tmp + flush + fsync + os.replace）
- 写入前**自动备份**到 `backups/` 目录
- **空数据保护**：如果即将写入空内容而旧文件非空，拒绝覆盖并记录警告
- **记忆污染防护**：`parse_memory_items()` 自动过滤 ```json、[、]、``` 等垃圾；保存前二次校验
- Web 面板「🧹 清理垃圾」按钮可一键清理已有污染（自动备份后执行）
- 切换世界、切换模型、重启 main.py **不清空任何记忆**

---

# 📊 运行时状态文件

## data/runtime_state.json

保存运行时选择，例如：

```json
{
  "active_world": "one"
}
```

由 WorldManager 管理。Web 切换世界时自动更新。

## data/provider_state.json

保存 provider 运行时状态，例如：

```json
{
  "mode": "auto",
  "manual_provider": null,
  "providers": {
    "zhipu_glm_air": {
      "consecutive_failures": 0,
      "cooldown_until": null,
      "exhausted": false,
      "last_error_type": null
    }
  },
  "last_fallback_time": null,
  "last_fallback_reason": null
}
```

由 LLMRouter 管理。provider 失败、冷却、耗尽状态都记录在此。

---

# ⚙ 环境变量

创建：

```bash
cp .env.example .env
nano .env
```

核心配置：

| 变量 | 说明 |
|------|------|
| BOT_TOKEN | Telegram Bot Token |
| ALLOWED_ID | 允许使用 Bot 的 Telegram 用户 ID |
| ACTIVE_WORLD | 默认世界（启动兜底，Web 面板可通过世界编辑器热切换） |
| WEB_PORT | Web 面板端口（默认 8080） |
| WEB_HOST | Web 监听地址（0.0.0.0=外网可访问） |
| WEB_PASSWORD | Web 面板登录密码（公网必设！） |

API Key（与 providers.yaml 中 `api_key_env` 对应）：

| 变量 | 对应 Provider |
|------|--------------|
| ZHIPU_API_KEY | zhipu_glm_air |
| DEEPSEEK_API_KEY | deepseek_v4_flash |
| OPENROUTER_API_KEY | openrouter_qwen_235b |
| VSLLM_API_KEY | 自定义 VSLLM provider |
| ... | 任意自定义 provider |

完整配置项及详细调参指南见 `config/settings.py` 注释。

---

# 🚀 VPS 部署

Ubuntu 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv git tmux

git clone <你的仓库地址>
cd <项目目录>

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
nano .env

python main.py
```

---

# 🌐 Web 管理面板

Bot 内置 Flask Web 管理面板，定位为 **"AI 角色扮演导演台"**。

## 配置

```env
WEB_PORT=8080
WEB_HOST=0.0.0.0     # 外网可访问（必须设 WEB_PASSWORD！）
WEB_PASSWORD=xxxxx    # 登录密码（用户名 admin）
```

> ⚠️ `WEB_HOST=0.0.0.0` 且未设 `WEB_PASSWORD` 时，项目**拒绝启动**。

## 访问

```
http://你的VPS_IP:8080
```

用户名 `admin`，密码为 `.env` 中设置的 `WEB_PASSWORD`。

## 功能

| 页面 | 功能 |
|------|------|
| 📊 仪表盘 | 运行状态、记忆/关系/时间统计、NPC 状态、最近异常 |
| ⚙ 配置中心 | 剧情与行为参数管理（回复长度、上下文、记忆、NPC、时间、关系） |
| 🌍 世界编辑器 | 热切换世界、新建/复制/删除、表单编辑、Prompt 预览 |
| 🔌 模型管理 | Provider 状态、自动/手动模式、测试连接、调用历史 |
| 🧠 记忆管理 | 短期记忆浏览 + 长期记忆增删改精炼 + 诊断信息 |
| 🔍 记忆检查 | 长期记忆污染检查（规则 + AI 两层） |
| 💞 关系网络 | 角色关系编辑（6 维度 + 死锁机制） |
| ⏰ 时间与节奏 | 时间状态编辑、快速推进、剧情节奏控制 |
| 📜 日志 | 查看最近日志，敏感信息自动过滤 |

## 安全

- Session 登录（Cookie: httponly + samesite=lax）
- 登录失败 3 次后冷却 30 分钟
- CSRF 保护
- 安全响应头
- 操作审计日志
- 敏感信息自动过滤

---

# 🔧 常见错误说明

| 错误 | 分类 | 处理 |
|------|------|------|
| `429 Too Many Requests` | rate_limited | 冷却 30-120s，自动 fallback |
| `503 Service Unavailable` | unavailable | 冷却 60-300s，自动 fallback |
| `500/502/504` | unavailable | 同上 |
| `insufficient balance` | quota_exhausted | 永久跳过（需手动清除） |
| `No available channel for model xxx under group free` | no_channel | 冷却 10-30min，自动 fallback |
| `model_not_found` | model_not_found | 标记配置错误，永久跳过 |
| `401/403 invalid api key` | auth_error | 标记 Key/权限错误，永久跳过 |

Web 面板的模型管理页可以查看每个 provider 的具体失败类型和状态。

---

# 💾 备份与恢复

## 自动备份

项目在以下操作前自动备份：
- 记忆写入（备份到 `backups/` 目录，保留最近 20 个）
- providers.yaml 修改（备份到 `backups/` 目录）
- 世界 YAML 保存（备份到 `backups/worlds/` 目录）
- /reset 命令（备份旧记忆）

## 手动恢复

```bash
# 查看备份
ls backups/

# 恢复记忆（示例）
cp backups/memory_one_chat_20250608_120000.json data/sessions/one_chat.json
```

---

# 🎭 NPC主动行为系统

（见上文已有详细说明，此处保留概要）

NPC 行为由主模型在一次回复中生成，零额外 API 成本。通过权重 + 全局概率 + 冷却三重控制频率。

---

# 💞 关系网络系统

追踪角色间 6 维度关系数值（好感、信任、畏惧、依赖、怀疑、敌意）。死锁机制：设为 110/-100 后自动抽取不再修改。

---

# ⏰ 时间流逝系统

用户驱动：关键词检测推进。手动命令：`/next_time` `/next_day`。续写不推进时间。

---

# 🗺 Roadmap

* [x] Telegram Bot
* [x] 多世界系统 + 热切换
* [x] 短期记忆 + 长期记忆
* [x] 自动记忆抽取与压缩
* [x] 多模型供应商路由 (LLM Router)
* [x] NPC 主动行为系统
* [x] 关系网络系统
* [x] Web 管理面板
* [x] 配置中心
* [x] 记忆污染检查
* [x] 剧情节奏控制
* [x] 时间流逝系统
* [x] 原子写入 + 自动备份
* [ ] 世界状态数据库
* [ ] 多角色同时对话
* [ ] 自动事件系统

---

# 📜 License

个人研究使用。
