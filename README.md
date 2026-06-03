# Telegram AI Roleplay Bot

一个用于 Telegram 的 AI 角色扮演 Bot。项目目标是让世界观、角色设定和剧情规则彻底脱离主程序：改剧情只改 `worlds/` 文件，主程序不动。

## 功能

- Telegram Bot 私聊使用
- DeepSeek API 对话生成
- 单用户授权
- 多世界切换
- 短期记忆
- 长期记忆
- 自动长期记忆抽取
- 旧对话摘要压缩
- `/start` 启动当前世界
- `/status` 查看状态
- `/memo` 写入长期记忆
- `/reset` 二次确认重置当前世界记忆
- `/c` 和 `/continue` 续写
- 长回复自动分段
- 回复被模型截断时提示续写

## 项目结构

```text
project/
├── main.py
├── config/
│   ├── settings.py
│   └── prompts.py
├── worlds/
│   ├── one.py
│   ├── two.py
│   └── three.py
├── memory/
│   ├── one_memory.json
│   └── one_world_memory.json
├── bot/
│   ├── telegram_handlers.py
│   ├── deepseek_client.py
│   ├── memory_manager.py
│   └── utils.py
├── requirements.txt
├── .gitignore
├── README.md
└── .env.example
```


## VPS 部署步骤

以下以 Ubuntu VPS 为例。

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git tmux

git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
```

在 `.env` 中填写：

```bash
BOT_TOKEN=你的TelegramBotToken
DEEPSEEK_KEY=你的DeepSeekKey
ALLOWED_ID=你的Telegram用户ID
ACTIVE_WORLD=one
```

启动：

```bash
python main.py
```

## tmux 后台运行

```bash
tmux new -s roleplay-bot
cd 你的仓库名
source venv/bin/activate
python main.py
```

按 `Ctrl+B`，再按 `D`，即可离开 tmux，Bot 会继续运行。

重新进入：

```bash
tmux attach -t roleplay-bot
```

停止 Bot：

```bash
tmux attach -t roleplay-bot
Ctrl+C
```

## GitHub 同步

第一次提交：

```bash
git init
git add .
git commit -m "Initial roleplay bot project"
git branch -M main
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

日常提交：

```bash
git status
git add .
git commit -m "Update world settings"
git push
```

VPS 更新代码：

```bash
cd 你的仓库名
git pull
source venv/bin/activate
pip install -r requirements.txt
```

如果 Bot 正在 tmux 中运行，更新后进入 tmux，按 `Ctrl+C` 停止，再执行 `python main.py`。

## 创建新世界

复制一个已有世界文件：

```bash
cp worlds/one.py worlds/new_world.py
```

编辑：

```bash
nano worlds/new_world.py
```

至少修改这些字段：

- `WORLD_NAME`
- `SYSTEM_PROMPT`
- `START_SCENE`
- `CHARACTERS`
- `RULES`
- `LOCATIONS`
- `EVENT_POOL`

例如文件名是 `worlds/new_world.py`，建议：

```python
WORLD_NAME = "new_world"
```

主程序不需要修改。

## 切换世界

编辑 `.env`：

```bash
ACTIVE_WORLD=one
```

可改成：

```bash
ACTIVE_WORLD=two
```

或：

```bash
ACTIVE_WORLD=three
```

保存后重启 Bot：

```bash
python main.py
```

## 记忆文件位置

记忆文件按世界自动隔离，存放在 `memory/`。

例如当前世界是 `one`：

```text
memory/one_memory.json
memory/one_world_memory.json
```

当前世界是 `two`：

```text
memory/two_memory.json
memory/two_world_memory.json
```

切换世界不会混用记忆。

## 新 VPS 迁移

推荐只迁移代码和 `.env`，记忆文件按需要单独复制。

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git tmux

git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
python main.py
```

如需迁移记忆，把旧 VPS 的 `memory/` 复制到新 VPS 项目目录即可。
