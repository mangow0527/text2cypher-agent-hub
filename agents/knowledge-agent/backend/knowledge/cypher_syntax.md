## Core Rules

[id: syntax_direction_rule]
- 优先使用 schema 中定义的显式方向，不要依赖双向匹配。

[id: syntax_with_rule]
- 聚合或多阶段过滤时，优先使用显式 `WITH` 分段，确保 TuGraph 可执行性。

[id: cypher_syntax]
- 关键字必须拼写正确，严禁出现拼写错误（如 'MATCHH', 'RETUR'）。
- 所有圆括号 '()' 和方括号 '[]' 必须成对闭合。
- RETURN 子句必须存在且语法完整，禁止返回未定义的变量。
- 语句结构应完整，包含 MATCH, WHERE (可选), RETURN 等必要子句。
- 建议在非聚合查询中显式添加 LIMIT 子句以防止数据量过大。
