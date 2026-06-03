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
* GitHub 版本管理
* VPS 快速迁移

---

# 🏗 架构设计

```text
用户
 │
 ▼
Telegram
 │
 ▼
telegram_handlers.py
 │
 ▼
deepseek_client.py
 │
 ▼
DeepSeek API
 │
 ▼
生成回复

       ▲
       │
memory_manager.py
       │
       ▼

短期记忆
长期记忆

       ▲
       │
worlds/*.py
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
│   └── utils.py
│
├── config/
│   ├── settings.py
│   └── prompts.py
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

说明：

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

cd 你的仓库

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
cd 项目目录

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

# 🔄 GitHub 工作流

提交修改：

```bash
git add .

git commit -m "update"

git push
```

服务器同步：

```bash
git pull
```

如果依赖更新：

```bash
pip install -r requirements.txt
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

如果需要迁移记忆：

```text
复制 memory/
目录即可
```

---

# 🗺 Roadmap

* [x] Telegram Bot
* [x] 多世界系统
* [x] 短期记忆
* [x] 长期记忆
* [x] 自动记忆抽取
* [x] GitHub 同步
* [ ] NPC 主动行为系统
* [ ] 世界状态数据库
* [ ] 多角色同时对话
* [ ] 自动事件系统
* [ ] 关系网络系统
* [ ] Web 管理面板

---

# 📜 License

个人研究使用。
