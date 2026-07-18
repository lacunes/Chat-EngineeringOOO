# 日常运维

本文件的启动、tmux 与 systemd 章节面向 Linux 服务器；本地开发默认使用
Windows 11 + PowerShell 7，测试命令见下文“本地回归测试”。

## 启动与停止

### 手动启动 bot

```bash
cd 项目目录
source venv/bin/activate      # 如果有虚拟环境
python main.py
```

Bot 启动后，Web 管理面板也在同一进程中（守护线程），默认监听 `http://0.0.0.0:8080`。安装依赖后会优先使用 Waitress；若依赖缺失才会明确告警并回退 Flask 开发服务器。
Telegram 命令菜单注册属于非关键初始化；Telegram API 短暂超时只会记录 warning，不会阻断 Web 面板或 polling 启动。

### tmux 后台运行

```bash
# 创建会话
tmux new -s roleplay

# 在 tmux 内启动
cd 项目目录
source venv/bin/activate
python main.py

# 离开 tmux（Bot 继续运行）
Ctrl+B, D

# 重新进入
tmux attach -t roleplay

# 停止 Bot
Ctrl+C
```

### 停止 bot

在 tmux 会话中按 `Ctrl+C`，或在终端中直接 `Ctrl+C`。

---

## 日志查看

所有日志在 `logs/` 目录下：

| 文件 | 内容 | 查看命令 |
|------|------|----------|
| `logs/app.log` | 全部运行日志 | `tail -f logs/app.log` |
| `logs/error.log` | 仅错误 | `tail -f logs/error.log` |
| `logs/llm_usage.jsonl` | LLM 调用用量、延迟与 fallback 记录 | `tail -f logs/llm_usage.jsonl` |
| `logs/memory.log` | 记忆提取/压缩/精炼 | `grep "extract\|refine\|compress" logs/memory.log` |
| `logs/relation.log` | 抽取版本、stale 丢弃、逐维度 before/delta/after、正文数字警告 | `grep "RELATION_CHANGE\|stale\|numeric disclosure" logs/relation.log` |
| `logs/story.log` | 剧情状态加载/更新 | `cat logs/story.log` |
| `web_audit.log` | Web 面板操作审计 | `tail -f web_audit.log` |

---

## 安全巡检

```bash
bash scripts/security_check.sh
```

自动检查：`.env` 泄露、`secrets.json` 泄露、`.gitignore` 规则、疑似敏感信息扫描。

### 关系状态排查

- Web `/relations` 顶部只读调试卡展示当前世界、实例、revision、JSON 路径、最后修改来源和下一轮 Prompt 关系摘要。
- `[RELATION_CHANGE]` 日志中 `before`/`after` 是当前总值，`delta` 必须带正负号。
- `stale relation extraction discarded` 表示抽取期间关系版本已变化，旧结果被安全丢弃。
- 只读扫描旧短期记忆中的关系面板式数字：`python scripts/audit_relation_numbers.py`。脚本只报告，不清理或改写消息。

---

## 健康检查与 systemd

轻量探活：

```bash
curl -fsS http://127.0.0.1:8080/health
```

未登录时只返回最小健康信息；登录后可看到 polling、当前世界、当前 provider、最近消息时间和连续 Telegram 网络错误计数。`/health` 不会主动请求 Telegram 或模型 API。

systemd 示例文件位于：

```bash
deploy/chat-engineering.service
```

部署时确认：

```bash
sudo cp deploy/chat-engineering.service /etc/systemd/system/chat-engineering.service
sudo chown root:root /etc/systemd/system/chat-engineering.service
sudo chmod 644 /etc/systemd/system/chat-engineering.service
sudo chown tianhai-bot:tianhai-bot /opt/tianhai-bot/Chat-EngineeringOOO/.env
sudo chmod 600 /opt/tianhai-bot/Chat-EngineeringOOO/.env
sudo systemctl daemon-reload
sudo systemctl enable --now chat-engineering.service
sudo systemctl status chat-engineering.service
```

健康诊断脚本：

```bash
bash scripts/health_watch.sh
tail -f logs/health_watch.log
```

脚本每分钟记录本机 `/health`、`main.py` 进程和 `api.telegram.org` 可达性，只用于诊断，不自动杀进程。

---

## Git 操作

### 世界数据文件位置

世界数据现在使用 `data/worlds/*.yaml`（YAML 格式，手动编辑友好）。
仓库内不再为同一世界保留 JSON/YAML 双份副本。仅有 JSON 的旧世界仍可兼容读取，
但 YAML 一旦存在就作为唯一权威来源。
`worlds/*.py` 已废弃删除，不再使用。

手动编辑世界时请直接编辑 `data/worlds/<世界名>.yaml`。
长文本字段（START_SCENE、SYSTEM_PROMPT）使用 YAML `|` 块文本格式，支持直接换行。

迁移 JSON-only 旧世界：

```powershell
python scripts/migrate_to_yaml.py
```

脚本默认跳过已有 YAML。确需覆盖时使用 `--overwrite`；覆盖前会备份旧 YAML 到
`backups/worlds/`。

### 本地回归测试

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q -p no:cacheprovider
```

测试应全部使用临时目录，不应改写 `data/provider_state.json`、真实世界文件或 `.env`。

### 记忆文件位置（v3）

| 文件 | 说明 |
|------|------|
| `data/sessions/{world}_chat.json` | 短期聊天上下文 |
| `data/memory/{world}_memories.json` | v3 结构化长期记忆（MemoryItem 格式） |
| `data/memory/{world}_long_term.json` | v2 旧格式（自动迁移到 v3，首次启动时备份） |
| `data/memory/{world}_summary.json` | 压缩摘要历史 |
| `data/state/{world}_relationships.json` | 关系网络状态 |
| `data/state/{world}_time_state.json` | 时间状态 |
| `data/state/{world}_story_state.json` | 剧情状态 |

首次启动时，若 `data/state/` 缺少对应文件而旧 `memory/` 中存在有效 JSON，会保留旧文件并复制到新目录。确认迁移无误前不要手动删除旧文件。

### 拉取更新前

如果担心 `data/worlds/` 下的世界文件被覆盖：

```bash
# 备份世界文件
cp -r data/worlds data/worlds_backup

# 拉取
git pull

# 如有冲突，手动恢复
```

### 不要提交的内容

`.gitignore` 已排除：`.env`、`logs/`、`memory/`、`*.bak`、`secrets.json`、`web_audit.log`。正式 `docs/` 会纳入版本控制；只应在其中保留不含敏感信息的文档。

---

## 常见问题排查

### Bot 无响应

1. 检查 tmux 是否还在运行：`tmux ls`
2. 检查 `logs/app.log` 最后几行：`tail -20 logs/app.log`
3. 检查 `logs/error.log`：`tail -20 logs/error.log`
4. 检查 Telegram 是否被封或限流

### API 报错

多供应商架构下，某个供应商不可用时系统会自动 fallback 到下一个。
查看具体错误：

1. 检查 `logs/error.log` 中的 LLM 相关错误
2. 检查 `logs/llm_usage.jsonl` 最后几条的 success 字段和 provider 字段
3. 访问 Web 面板「模型管理」查看各 provider 的实时状态
4. 确认对应 API Key（ZHIPU_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY）在 `.env` 中且未过期
5. 使用 Web 面板「测试连接」按钮排查具体 provider

常见 API 错误：
- `401` → API Key 无效，检查 `.env` 中对应 provider 的 Key
- `429` → 频率限制，稍等重试（或自动 fallback）
- `500` → 服务端问题，稍等重试
- `Timeout` → 网络问题或模型响应慢
- `quota_exhausted` → 额度/余额耗尽，provider 被自动永久跳过（可通过 Web 面板清除）

### Telegram 报错

1. 检查 `BOT_TOKEN` 是否正确
2. 确认 Bot 未被封禁
3. 检查 `ALLOWED_ID` 是否匹配

---

## 配置修改

大部分配置可以在 Web 管理面板的「配置中心」修改（`http://你的IP:8080/config`）。

公网监听（`WEB_HOST=0.0.0.0`）必须同时配置 `WEB_PASSWORD` 和独立高熵的 `WEB_SESSION_SECRET`。后者只用于签名 Web session，不能复用登录密码或 API Key。

**以下配置修改后需要重启 Bot 进程**：
- `WEB_PORT`、`WEB_HOST`
- `DEEPSEEK_THINKING`（思考模式开关）

**以下配置已支持运行时即时生效，无需重启**：
- `ACTIVE_WORLD`（切换世界）→ Web 面板世界编辑器点击「切换」即可，下次聊天即时生效
- 模型管理 → Web 面板「模型管理」页面，切换 provider / 启用禁用 / 修改模式均即时生效
- `providers.yaml` → 修改后自动热重载
- `data/worlds/*.yaml` → 编辑保存后自动热重载

重启方法：在 tmux 中 `Ctrl+C` 停止，重新 `python main.py`。

### 模型管理（Web 面板）

访问 `http://你的IP:8080/providers` 进入模型管理页面：
- 查看所有 provider 的状态（启用/禁用/冷却/耗尽/失败次数）
- 切换自动/手动模式
- 手动模式下选择优先 provider
- 启用/禁用 provider，自动备份 providers.yaml
- 清除 provider 的失败/冷却/耗尽状态
- 测试连接（发"请只回复 OK"，显示延迟和返回内容）
- 查看最近 20 次 LLM 调用记录
- 预览当前世界实际 Prompt

### 多供应商说明

项目使用 LLM Router 管理多个模型供应商，按优先级自动 fallback。
当前支持的供应商配置在 `providers.yaml` 中（已配置 zhipu_glm_air / deepseek_v4_flash / openrouter_qwen_235b）。
各供应商的 API Key 在 `.env` 中对应配置（如 `ZHIPU_API_KEY`、`DEEPSEEK_API_KEY`、`OPENROUTER_API_KEY`）。

- 默认自动模式：按 priority 顺序尝试，失败自动 fallback
- 冷却机制：连续失败 N 次进入冷却
- 额度耗尽：自动永久跳过
- 手动模式：可在 Web 面板指定优先使用某个 provider
