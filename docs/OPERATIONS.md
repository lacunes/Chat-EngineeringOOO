# 日常运维

## 启动与停止

### 手动启动 bot

```bash
cd 项目目录
source venv/bin/activate      # 如果有虚拟环境
python main.py
```

Bot 启动后，Web 管理面板也在同一进程中（守护线程），默认监听 `http://0.0.0.0:8080`。

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
| `logs/api_usage.jsonl` | API 用量+缓存命中率 | `tail -f logs/api_usage.jsonl` |
| `logs/memory.log` | 记忆提取/压缩/精炼 | `grep "extract\|refine\|compress" logs/memory.log` |
| `logs/relation.log` | 关系分析触发/变化 | `grep "extraction\|delta" logs/relation.log` |
| `logs/story.log` | 剧情状态加载/更新 | `cat logs/story.log` |
| `web_audit.log` | Web 面板操作审计 | `tail -f web_audit.log` |

---

## 安全巡检

```bash
bash scripts/security_check.sh
```

自动检查：`.env` 泄露、`secrets.json` 泄露、`.gitignore` 规则、疑似敏感信息扫描。

---

## Git 操作

### 拉取更新前

如果担心 `worlds/` 下的世界文件被覆盖：

```bash
# 备份世界文件
cp -r worlds worlds_backup

# 拉取
git pull

# 如有冲突，手动恢复
```

### 不要提交的内容

`.gitignore` 已排除：`.env`、`logs/`、`memory/`、`*.bak`、`secrets.json`、`web_audit.log`。

---

## 常见问题排查

### Bot 无响应

1. 检查 tmux 是否还在运行：`tmux ls`
2. 检查 `logs/app.log` 最后几行：`tail -20 logs/app.log`
3. 检查 `logs/error.log`：`tail -20 logs/error.log`
4. 检查 Telegram 是否被封或限流

### API 报错

1. 检查 `logs/error.log` 中的 DeepSeek 相关错误
2. 检查 `logs/api_usage.jsonl` 最后几条的 success 字段
3. 确认 `DEEPSEEK_KEY` 在 `.env` 中且未过期
4. 检查余额：登录 platform.deepseek.com

常见 API 错误：
- `401` → API Key 无效
- `429` → 频率限制，稍等重试
- `500` → DeepSeek 服务端问题，稍等重试
- `Timeout` → 网络问题或模型响应慢

### Telegram 报错

1. 检查 `BOT_TOKEN` 是否正确
2. 确认 Bot 未被封禁
3. 检查 `ALLOWED_ID` 是否匹配

---

## 配置修改

大部分配置可以在 Web 管理面板的「配置中心」修改（`http://你的IP:8080/config`）。

部分配置修改后需要**重启 Bot 进程**才生效：
- `ACTIVE_WORLD`（切换世界）
- `MODEL_NAME`（切换模型）
- `WEB_PORT`、`WEB_HOST`

重启方法：在 tmux 中 `Ctrl+C` 停止，重新 `python main.py`。
