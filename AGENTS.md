# AGENTS.md — frida-test workspace

当前主项目位于：

`/home/yuyang/frida-test/.claude/skills/smart-fund-server`

修改该项目代码时，必须同时遵守：

- `/home/yuyang/frida-test/CLAUDE.local.md`
- `/home/yuyang/frida-test/.claude/skills/smart-fund-server/AGENTS.md`

其中 `smart-fund-server/AGENTS.md` 包含 Milvus / PyMilvus 编码约束：统一使用 `MilvusClient`、使用 `DataType` 枚举、优先 `target_id` 作为 Milvus `VARCHAR` 主键、索引刷新使用 `upsert()`、PG 不保存完整 chunk/card text、Milvus 负责语义搜索和按 ID 精准取回。
