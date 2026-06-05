# Telegram AI Roleplay Framework

一个基于 Telegram + DeepSeek 的长期记忆角色扮演框架。

项目目标：

> 让世界观、角色设定、剧情规则与机器人核心逻辑彻底解耦。
> 修改剧情时只需要编辑世界文件，而不需要修改主程序。

---

# ✨ 特性

* Telegram 私聊角色扮演
* DeepSeek API 驱动
* 单用户授权
* 多世界切换
* 短期记忆
* 长期记忆
* 自动记忆提取
* 历史对话摘要压缩
* 自动续写
* 长回复自动分段
* NPC主动行为系统
* Web 管理面板
* GitHub 版本管理
* VPS 快速迁移

---

# 🏗 架构设计

```text
用户 ──→ Telegram ──→ telegram_handlers.py ──→ deepseek_client.py ──→ DeepSeek API
 │                         │                                              │
 │                    npc_manager.py                                      │
 │                    (NPC主动行为)                                        │
 │                         │                                              │
 │                    memory_manager.py ←─────────────────────────────────┘
 │                         │
 │                    ┌────┴────┐
 │                    │         │
 │                 短期记忆  长期记忆
 │
 └──────────────── worlds/*.py (世界观/NPC/规则)
```

---

# 📁 项目结构

```text
project/
├── main.py
│
├── bot/
│   ├── telegram_handlers.py
│   ├── deepseek_client.py
│   ├── memory_manager.py
│   ├── npc_manager.py
│   └── utils.py
│
├── config/
│   ├── settings.py
│   └── prompts.py
│
├── web/
│   ├── app.py
│   └── templates.py
│
├── worlds/
│   ├── one.py
│   ├── two.py
│   └── three.py
│
├── memory/
│
├── requirements.txt
├── .gitignore
├── .env.example
└── README.md
```

---

# 🌍 世界观系统

所有剧情均位于：

```text
worlds/
```

例如：

```text
worlds/
├── one.py
├── two.py
└── three.py
```

每个世界都拥有自己的：

* SYSTEM_PROMPT
* START_SCENE
* CHARACTERS
* RULES
* LOCATIONS
* EVENT_POOL

---

# 🧠 记忆系统

项目拥有两层记忆。

## 短期记忆

用于维持当前对话连续性。

例如：

* 正在发生的事件
* 当前人物关系
* 最近聊天内容

文件：

```text
memory/one_memory.json
```

---

## 长期记忆

用于记录世界状态变化。

例如：

* 人物关系变化
* 身份暴露
* 重要剧情事件
* 阵营变化
* 世界观推进

文件：

```text
memory/one_world_memory.json
```

长期记忆会自动参与后续生成。

---

# 🎭 NPC主动行为系统

NPC不再只是被动响应用户——他们有**自己的行为规律**，会在合适的时机主动进入故事。

## 工作原理

```
用户发消息
  │
  ├─ 1. npc_manager 检查冷却 + 概率判断
  │     触发条件: NPC权重 × 全局基础概率
  │
  ├─ 2. 为触发的NPC生成"舞台指令"
  │     例如: "【旅馆老板】性格热情。可采取的行动: 端茶、搭话..."
  │
  ├─ 3. 舞台指令注入 system prompt
  │
  └─ 4. AI在回复中自然融入NPC行为
         "谈话间，旅馆老板端着一壶热茶走了过来..."
```

**关键特性**:
- 💰 **零额外API成本** —— NPC行为由主模型在一次回复中生成
- 🎛️ **可控频率** —— 权重 + 全局概率 + 冷却三重控制
- 🔌 **即插即用** —— 世界文件定义NPC，无需修改主程序
- ⏱️ **双重触发** —— 用户消息驱动 + 后台定时器

## 定义NPC

在 `worlds/one.py` 的 `NPCS` 字典中配置：

```python
NPCS: dict[str, dict] = {
    "innkeeper": {
        "name": "旅馆老板老张",
        "description": "镇上唯一旅馆的老板",
        "personality": "热情、健谈、爱打听消息",
        "goals": ["让客人住得舒服", "打听镇上新鲜事"],
        "typical_actions": [
            "主动和客人搭话",
            "端来热茶招待客人",
            "分享镇上小道消息",
        ],
        "activation_weight": 0.35,     # 触发权重 (0~1)
        "cooldown_messages": 10,        # 冷却消息数
    },
}
```

**触发概率** = NPC权重 × `NPC_BASE_ACTIVATION` (`.env` 中设置，默认 0.5)

例如: `0.35 × 0.5 = 17.5%` 概率每次用户消息触发该NPC。

## 配置参数

在 `.env` 中调整：

```env
NPC_BASE_ACTIVATION=0.5             # 全局基础概率 (0~1)，0=完全禁用
NPC_MAX_ACTIONS_PER_CHECK=1         # 单次最多触发几个NPC
NPC_TIMER_INTERVAL=300              # 后台定时器间隔（秒）
NPC_ACTION_MAX_TOKENS=200           # 舞台指令最大token
NPC_TIMER_ACTIVATION_MULTIPLIER=0.6 # 定时器模式概率折半系数
NPC_CONTEXT_BOOST_MULTIPLIER=2.0    # 关键词命中时概率翻倍系数
```

---

# ⚙ 环境变量

创建：

```bash
cp .env.example .env
```

编辑：

```bash
nano .env
```

配置：

```env
BOT_TOKEN=xxxxx
DEEPSEEK_KEY=xxxxx
ALLOWED_ID=123456789
ACTIVE_WORLD=one
```

说明（完整配置项及详细调参指南见 `config/settings.py` 注释）：

| 变量           | 说明                     |
| ------------ | ---------------------- |
| BOT_TOKEN    | Telegram Bot Token     |
| DEEPSEEK_KEY | DeepSeek API Key       |
| ALLOWED_ID   | 允许使用 Bot 的 Telegram 用户 |
| ACTIVE_WORLD | 当前加载世界                 |

---

# 🚀 VPS 部署

Ubuntu 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv git tmux

git clone https://github.com/lacunes/Chat-EngineeringOOO.git

cd Chat-EngineeringOOO

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
nano .env

python main.py
```

---

# 🔧 tmux 后台运行

创建：

```bash
tmux new -s roleplay
```

启动：

```bash
cd Chat-EngineeringOOO

source venv/bin/activate

python main.py
```

离开：

```text
Ctrl+B
D
```

Bot 将继续运行。

---

重新进入：

```bash
tmux attach -t roleplay
```

停止：

```text
Ctrl+C
```


---

# 🆕 创建新世界

复制：

```bash
cp worlds/one.py worlds/new_world.py
```

编辑：

```bash
nano worlds/new_world.py
```

建议修改：

```python
WORLD_NAME
SYSTEM_PROMPT
START_SCENE
CHARACTERS
RULES
LOCATIONS
EVENT_POOL
```

无需修改主程序。

---

# 🔀 切换世界

编辑：

```env
ACTIVE_WORLD=one
```

例如：

```env
ACTIVE_WORLD=two
```

保存后重启 Bot：

```bash
python main.py
```

---

# 💾 迁移到新 VPS

安装环境：

```bash
sudo apt update

sudo apt install -y python3 python3-venv git tmux
```

拉取项目：

```bash
git clone https://github.com/lacunes/Chat-EngineeringOOO.git

cd Chat-EngineeringOOO
```

创建环境：

```bash
python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt
```

配置：

```bash
cp .env.example .env

nano .env
```

启动：

```bash
python main.py
```

---

# 🌐 Web 管理面板

Bot 内置 Flask Web 管理面板，可通过浏览器进行后台操作。

## 配置

在 `.env` 中设置：

```env
WEB_PORT=8080        # 监听端口
WEB_HOST=0.0.0.0     # 0.0.0.0=外网可访问, 127.0.0.1=仅本机
WEB_PASSWORD=xxxxx   # 登录密码（用户名 admin），留空则不设密码
```

## 访问

```text
http://你的VPS_IP:8080
```

浏览器会弹出认证框，输入用户名 `admin` 和设定的密码。

## 功能

| 页面 | 功能 |
|------|------|
| 📊 仪表盘 | 运行状态、记忆统计、NPC 状态、一键重置 |
| 💬 短期记忆 | 浏览最近对话记录，30 秒自动刷新 |
| 🧠 长期记忆 | 查看/新增/删除/精炼长期记忆条目 |
| 🌍 世界管理 | 列出所有世界文件、在线编辑代码、切换活跃世界 |
| 📜 日志 | 查看最近 100 行 bot.log，15 秒自动刷新 |

## 安全建议

- 务必设置 `WEB_PASSWORD`，否则面板完全无保护
- 若只需本地访问，将 `WEB_HOST` 改为 `127.0.0.1`，配合 SSH 隧道使用：
  ```bash
  ssh -L 8080:127.0.0.1:8080 user@你的VPS_IP
  ```
  然后访问 `http://localhost:8080`

---

# 💞 关系网络系统

追踪角色之间的关系数值变化，为长期剧情提供结构化支撑。

## 工作原理

```
用户消息 → AI 回复 → 后台每 2 轮抽取关系变化
                         │
                         ├─ 轻量 DeepSeek API 分析对话
                         ├─ JSON 输出变化量 (±1~3，重大事件可超出)
                         └─ 下一条回复开头显示: （A→B：信任+2）
```

## 数据结构（memory/one_relationships.json）

```json
{
  "characters": ["A", "B"],
  "relations": {
    "A->B": {
      "affection": 65, "trust": 68, "fear": 10,
      "dependence": 42, "suspicion": 15, "hostility": 5,
      "notes": ["初次见面时救过对方"],
      "last_updated": 42
    }
  }
}
```

六个维度（0-100）：好感、信任、畏惧、依赖、怀疑、敌意。非对称关系。

> **死锁机制**：在 Web 面板将某维度设为 110，该维度将被锁死，不再随对话自动变化。显示时加 🔒 标记。正常对话变化范围仍为 0~100。

## 如何影响 AI 回复

关系摘要会注入 system prompt 并附带行为指令：

- **好感/信任高** → 语气友好、愿意合作、主动帮助
- **畏惧/敌意高** → 语气警惕、保持距离、可能拒绝合作
- **怀疑高** → 话中有话、保留信息、试探性提问
- **依赖高** → 主动求助、犹豫不决、寻求认可
- **数值变化** → 在对话中自然体现，不会直接引用数字

## Telegram 命令

| 命令 | 说明 |
|------|------|
| `/relations` | 显示当前世界角色关系摘要 |
| `/relation_full` | 显示完整关系网络（含备注、历史轮次） |

## 配置

```env
RELATION_EXTRACT_INTERVAL=2       # 每 N 次 AI 回复触发抽取
RELATION_SIGNIFICANT_THRESHOLD=3  # 变化超过此值标记 ⚡
```

---

# ⏰ 时间流逝系统

为角色生活提供时间背景，不强行推动剧情。

## 工作原理

```
每次 AI 回复 → 检查轮次间隔
  ├─ 未到阈值 → 不动
  └─ 到达阈值 → 时段 +1（清晨→上午→...→深夜→清晨+1天）
```

- 默认每 6 次 AI 回复自动推进一个时段
- 用户可手动 `/next_time` 或 `/next_day`
- 推进到新一天时自动生成"昨日生活摘要"

## 时间注入

system prompt 会包含时间背景：

```
[当前时间]
第3天，初夏，下午。
近日：入住旅馆；集市偶遇旅人。
```

## Telegram 命令

| 命令 | 说明 |
|------|------|
| `/time` | 显示当前世界时间状态 |
| `/next_time` | 手动推进一个时段 |
| `/next_day` | 推进到第二天清晨，生成昨日摘要 |

## 配置

```env
TIME_ADVANCE_INTERVAL=6  # 每 N 次 AI 回复自动推进时段
```

---

# 🗺 Roadmap

* [x] Telegram Bot
* [x] 多世界系统
* [x] 短期记忆
* [x] 长期记忆
* [x] 自动记忆抽取
* [x] GitHub 同步
* [x] NPC 主动行为系统
* [ ] 世界状态数据库
* [ ] 多角色同时对话
* [ ] 自动事件系统
* [x] 关系网络系统
* [x] Web 管理面板
* [x] 时间流逝系统

---

# 📜 License

个人研究使用。
---

# 🔄 GitHub 工作流

提交修改：

```bash
git add .

git commit -m "update"

git push
```

服务器同步：

```bash
git restore .env.example worlds/one.py
git pull
```

依赖更新：

```bash
pip install -r requirements.txt
```

