## Core Rules

[id: syntax_direction_rule]
- 优先使用 schema 中定义的显式方向，不要依赖双向匹配。

[id: syntax_with_rule]
- 聚合或多阶段过滤时，优先使用显式 `WITH` 分段，确保 TuGraph 可执行性。
