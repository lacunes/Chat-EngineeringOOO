# 开发规则入口

项目唯一的开发治理规则是根目录的 [`DEVELOPMENT_RULES.md`](../DEVELOPMENT_RULES.md)。

本文件不复制规则正文，以免出现多份流程和状态目录说明漂移。开始开发任务时，应依次读取根目录 `DEVELOPMENT_RULES.md`、`SKILLS_ROUTING.md`（如存在）、`DEVELOPMENT_TASK_TEMPLATE.md` 和对应的 `development-tasks/` 任务单。

运行数据目录约定：

- `data/sessions/`：短期会话；
- `data/memory/`：结构化长期记忆和摘要；
- `data/state/`：关系、时间、剧情状态；
- `memory/`：仅保留为旧状态迁移来源，禁止新增运行数据。
