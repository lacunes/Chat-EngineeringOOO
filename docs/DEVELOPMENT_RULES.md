# 开发规则（给 AI 编码助手使用）

本文档供 Reasonix / Codex / Claude Code 等 AI 编码助手参考。
修改任何代码前请先阅读。

## 绝对不要做

- ❌ 不要删除长期记忆功能
- ❌ 不要减少短期上下文数量（`CONTEXT_LENGTH`）
- ❌ 不要关闭 NPC 主动行为
- ❌ 不要每轮新增后台 API 调用
- ❌ 不要为了缓存删除动态剧情信息
- ❌ 不要修改 `data/worlds/` 下的世界文本内容（除非用户明确要求）
- ❌ 不要恢复 Web 直接编辑 `.py` 世界文件
- ❌ 不要恢复 Telegram `/provider` 命令（模型管理已迁移至 Web 面板）
- ❌ 不要把动态状态（时间/关系/NPC/导演指令）放到固定 prompt 前面
- ❌ 不要提交 `.env`、`secrets.json`、API key、Telegram token
- ❌ 不要把真实密钥写入日志
- ❌ 不要大规模重构
- ❌ 不要改变主聊天体验
- ❌ 不要做网页终端、维护中心、系统运维功能
- ❌ 不要使用外部 CDN（CSS/JS 全部本地化）
- ❌ 不要暴露 BOT_TOKEN、DEEPSEEK_KEY、WEB_PASSWORD、ALLOWED_ID 等敏感配置

## 修改前先判断

每次修改代码前，请评估是否影响以下方面：

- [ ] **聊天体验**：回复质量、角色一致性、叙事风格是否受影响？
- [ ] **API 开销**：是否新增了 API 调用？是否提高了调用频率？
- [ ] **长期记忆**：记忆提取、保存、注入是否受影响？
- [ ] **关系系统**：关系分析触发频率、数据格式是否受影响？
- [ ] **剧情状态**：story_state.json 加载/保存是否受影响？
- [ ] **NPC 主动行为**：NPC 触发、舞台指令生成是否受影响？
- [ ] **Prompt 缓存结构**：固定→半固定→动态→对话 的四层顺序是否被破坏？
- [ ] **短期上下文**：最近 N 条聊天记录数量是否减少？
- [ ] **Web 面板**：路由、模板、CSRF/安全机制是否受影响？
- [ ] **数据兼容**：现有 JSON 文件格式是否仍可读取？

## 修改后应说明

- 修改了哪些文件
- 是否增加 API 调用
- 是否改变 prompt 结构
- 是否影响世界数据文件（`data/worlds/*.yaml`）
- 是否影响记忆、关系、剧情状态、NPC
- 是否改变聊天体验
- 潜在风险

## Prompt 组装规则（重要）

当前 prompt 的顺序是精心设计的，目的是最大化 DeepSeek prefix cache 命中率：

```
msg 0: [固定] world.SYSTEM_PROMPT + TIME_INJECT_INSTRUCTION
msg 1: [半固定] [长期记忆]...
msg 2: [动态] NPC指令 + 关系摘要 + 时间数据 + 剧情状态 + director指令
msg 3+: [对话] 最近 N 条聊天记录
```

**原则**：越不容易变的内容越靠前。永远不要把动态内容（关系摘要、时间数据、NPC 指令等）塞到 world.SYSTEM_PROMPT 同一个消息里。

## 数据文件位置

| 数据 | 路径 |
|------|------|
| 世界数据（主格式） | `data/worlds/*.yaml` |
| 世界数据（旧兼容） | `data/worlds/*.json` |
| 运行时状态 | `data/runtime_state.json` |
| Provider 配置 | `providers.yaml` |
| Provider 状态 | `data/provider_state.json` |
| 短期记忆 | `memory/{世界名}_memory.json` |
| 长期记忆 | `memory/{世界名}_world_memory.json` |
| 关系网络 | `memory/{世界名}_relationships.json` |
| 时间状态 | `memory/{世界名}_time_state.json` |
| 剧情状态 | `memory/{世界名}_story_state.json` |
| 记忆检查报告 | `memory_audit/memory-audit-*.json` |
| 导演指令 | `runtime_directive.json` |
| API 用量 | `logs/api_usage.jsonl` |
| LLM 调用记录 | `logs/llm_usage.jsonl` |
| 操作审计 | `web_audit.log` |

## 日志原则

- 日志写入失败不能导致 bot 崩溃
- 日志不输出完整 API key、Token、密码
- 专题日志（memory/relation/story）使用独立 logger，不传播到根 logger

## 用户偏好

- 用户不是专业程序员，解释时请分步骤、清晰
- 优先兼容现有结构，不要强行重写
- 小步修改，保证每一步都能运行
- 不引入复杂前端框架，优先 Flask + Jinja2 + 原生 CSS/JS
