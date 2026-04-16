# 系统联调工作台与运行架构文档设计稿

## Summary

本设计稿定义两个交付物：

1. 一个挂载在 `Testing Service` 下的系统级界面：
   - 名称：`系统联调工作台（System Integration Console）`
   - 目标：同时承担“系统架构展示”和“系统级联调操作”两类职责

2. 一份正式运行架构文档：
   - 路径：`/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`
   - 目标：清晰说明当前系统运行中的服务、边界、链路和数据流

本设计以当前实际运行架构为基础：

- `Cypher 生成服务（Cypher Generation Service, CGS）`：`8000`
- `测试服务（Testing Service）`：`8001`
- `知识修复建议服务（Knowledge Repair Suggestion Service, KRSS）`：`8002`
- `知识运营服务（Knowledge Ops / knowledge-agent）`：`8010`
- `QA 生成器（QA Generator / qa-agent）`：`8020`

其中，`Testing Service` 作为系统执行与评测中心，承载本次新增界面的挂载位置。

---

## 一、目标与非目标

### 1.1 目标

本次设计希望解决 4 个问题：

1. 让系统当前的运行架构可以被直观看见  
   包括服务关系、端口、职责边界、数据流。

2. 让系统主链路可以从一个页面发起联调  
   包括成功路径和失败闭环路径。

3. 让联调时的关键中间产物可以集中查看  
   包括 prompt、generated cypher、evaluation、issue ticket、knowledge repair suggestion。

4. 让界面和文档都采用中英双语表达  
   避免“只有英文术语可看、中文语义不清”的问题。

### 1.2 非目标

本次设计明确不包含：

- 不新增新的核心业务服务
- 不重构现有服务边界
- 不在本次设计中启用 KRSS 的 `experiment_runner`
- 不要求一次性把所有联调都汇总成新的后端 orchestration service
- 不试图替代单服务控制台（CGS / Testing / KRSS 各自已有控制台仍保留）

---

## 二、推荐方案

### 2.1 方案选择

采用**方案 B：双标签页（Two-Tab Workspace）**。

页面挂载在 `Testing Service` 下，提供两个标签页：

1. `架构总览（Architecture Overview）`
2. `系统联调（System Integration Console）`

### 2.2 选择理由

相比“单页全部堆叠”或“总览页 + 侧边抽屉”的方案，双标签页更适合当前项目：

- 信息分层清楚
- 兼顾“讲架构”和“真联调”
- 不会把页面做得过重
- 便于后续继续扩展更多联调细节
- 与当前 `Testing Service` 控制台的既有实现方式更兼容

---

## 三、挂载位置与整体落点

### 3.1 挂载服务

新界面挂载在：

`Testing Service（测试服务）`

理由：

- 它已经是当前系统的执行与评测中心
- 成功与失败路径都必须经过它
- 它最适合作为系统联调总控台

### 3.2 主要落点

优先在现有 Testing Service UI 基础上扩展：

- `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/index.html`
- `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/app.js`
- `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/styles.css`

### 3.3 正式文档落点

新增正式运行架构文档：

- `/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`

---

## 四、界面信息架构

## 4.1 顶层结构

页面整体结构分为三层：

1. 顶部总览头部
2. 标签页切换区
3. 标签页内容区

### 4.1.1 顶部总览头部

头部展示：

- 页面名称：`系统联调工作台（System Integration Console）`
- 当前运行模式摘要
- 关键服务状态摘要
- 当前联调链路提示

### 4.1.2 标签页

两个标签页：

- `架构总览（Architecture Overview）`
- `系统联调（System Integration Console）`

---

## 五、标签页一：架构总览

`架构总览（Architecture Overview）` 标签页用于展示系统当前真实运行架构，不承担主动执行链路的职责。

### 5.1 模块一：运行服务卡片

展示当前运行中的服务卡片，每张卡片都采用中英双语：

#### 卡片字段

- 中文服务名
- 英文服务名
- 缩写
- 端口
- 健康状态
- 核心职责
- 关键接口

#### 服务列表

1. `Cypher 生成服务（Cypher Generation Service, CGS）`
   - 端口：`8000`
   - 核心职责：接收 `id + question`、拉取 prompt、生成 Cypher、提交测试服务

2. `测试服务（Testing Service）`
   - 端口：`8001`
   - 核心职责：执行 TuGraph、做评测、失败时生成 `Issue Ticket`

3. `知识修复建议服务（Knowledge Repair Suggestion Service, KRSS）`
   - 端口：`8002`
   - 核心职责：读取失败证据和 prompt snapshot，生成知识修复建议

4. `知识运营服务（Knowledge Ops / knowledge-agent）`
   - 端口：`8010`
   - 核心职责：提供 prompt、接收知识修复建议

5. `QA 生成器（QA Generator / qa-agent）`
   - 端口：`8020`
   - 核心职责：外围问题生成/任务驱动组件

### 5.2 模块二：系统结构图

展示一张静态结构图，说明服务之间的依赖关系。

结构图需要明确显示：

- CGS -> Knowledge Ops：获取 prompt
- CGS -> Testing Service：提交生成结果
- Testing Service -> TuGraph：执行查询
- Testing Service -> KRSS：失败后发送 `Issue Ticket`
- KRSS -> CGS：读取 `Prompt Snapshot`
- KRSS -> Knowledge Ops：发送 `Knowledge Repair Suggestion`

### 5.3 模块三：正式数据流图

展示关键对象在系统中的流转。

#### 关键数据对象

- `问题请求（QA Question Request）`
- `提示词快照（Prompt Snapshot Response）`
- `评测提交（Evaluation Submission Request）`
- `问题单（Issue Ticket）`
- `知识修复建议（Knowledge Repair Suggestion Request）`

每个对象都展示：

- 中文名称
- 英文名称
- 来源服务
- 去向服务
- 业务含义

### 5.4 模块四：服务边界说明

用简明分区说明当前职责边界：

- CGS 负责生成，不负责执行
- Testing Service 负责执行与评测
- KRSS 负责知识修复建议，不负责业务裁决
- Knowledge Ops 负责知识输入与知识更新

### 5.5 模块五：接口速查表

列出联调最关键接口：

- `POST /api/v1/qa/questions`
- `GET /api/v1/questions/{id}/prompt`
- `POST /api/v1/qa/goldens`
- `POST /api/v1/evaluations/submissions`
- `POST /api/v1/issue-tickets`
- `POST /api/knowledge/rag/prompt-package`
- `POST /api/knowledge/repairs/apply`

---

## 六、标签页二：系统联调

`系统联调（System Integration Console）` 标签页用于发起系统级联调，并查看一轮联调的时序和结果。

### 6.1 支持的主路径

支持两条主路径：

#### 6.1.1 成功路径（Success Path）

`问题 -> CGS -> Testing Service -> 通过`

#### 6.1.2 失败闭环路径（Failure Closed Loop）

`问题 -> CGS -> Testing Service -> KRSS -> Knowledge Ops`

### 6.2 模块一：联调输入区

联调输入区字段：

- `任务标识（Task ID）`
- `问题原文（Question Text）`
- `联调路径（Run Mode）`
  - `成功路径（Success Path）`
  - `失败路径（Failure Closed Loop）`

辅助操作：

- 填充成功样例
- 填充失败样例
- 一键发起联调

### 6.3 模块二：链路阶段状态区

展示每个阶段的运行状态卡片：

- `Cypher 生成服务（CGS）`
- `测试服务（Testing Service）`
- `知识修复建议服务（KRSS）`
- `知识运营服务（Knowledge Ops）`

每张卡片状态：

- `未开始（Idle）`
- `进行中（Running）`
- `成功（Success）`
- `失败（Failed）`
- `跳过（Skipped）`

### 6.4 模块三：时序流视图

展示本轮联调的时序流（Sequence View）。

按时间顺序展示：

1. 问题进入 CGS
2. CGS 拉取 prompt
3. CGS 生成 Cypher
4. CGS 提交 Testing Service
5. Testing Service 执行 TuGraph
6. Testing Service 输出评测结果
7. 若失败：
   - 生成 `Issue Ticket`
   - 调用 KRSS
   - KRSS 拉取 `Prompt Snapshot`
   - KRSS 生成修复建议
   - KRSS 调用 Knowledge Ops

### 6.5 模块四：关键数据面板

联调结果区按卡片展示：

- `输入提示词快照（Input Prompt Snapshot）`
- `生成的 Cypher（Generated Cypher）`
- `评测摘要（Evaluation Summary）`
- `问题单（Issue Ticket）`
- `知识修复建议（Knowledge Repair Suggestion）`

每个面板：

- 先给中文解释
- 再给 JSON 原文

### 6.6 模块五：原始响应区

用于查看原始返回 JSON，方便联调和排障。

建议支持：

- CGS 原始响应
- Testing 状态快照
- KRSS 分析记录或写接口响应

---

## 七、中英双语规范

本次交付物必须执行：

**中文主述 + 英文术语对照**

### 7.1 页面文案规范

所有面向用户可读的架构与业务文案，首次出现时都要采用：

`中文名称（English Name, 缩写）`

例如：

- `Cypher 生成服务（Cypher Generation Service, CGS）`
- `知识修复建议服务（Knowledge Repair Suggestion Service, KRSS）`
- `问题单（Issue Ticket）`
- `输入提示词快照（Prompt Snapshot）`

### 7.2 JSON 字段展示规范

原始 JSON 区域保留英文原字段名，不做字段重命名。  
但在旁边或上方给出中文业务解释。

### 7.3 文档规范

正式文档中的服务名、对象名、接口名，首次出现必须中英对照。

---

## 八、数据源与接口复用策略

### 8.1 优先原则

优先复用已有接口，不新增无必要的聚合后端。

### 8.2 可直接复用的接口

#### CGS

- `POST /api/v1/qa/questions`
- `GET /api/v1/questions/{id}/prompt`

#### Testing Service

- `POST /api/v1/qa/goldens`
- `GET /api/v1/evaluations/{id}`
- `GET /api/v1/issues/{ticket_id}`
- `GET /api/v1/status`

#### KRSS

- `POST /api/v1/issue-tickets`
- `GET /api/v1/krss-analyses/{analysis_id}`

### 8.3 是否新增聚合接口

建议先不新增。  
前端先通过 Testing Service 页面内直接请求已有接口完成联调展示。

只有在出现以下问题时，才考虑新增轻量聚合接口：

- 前端请求次数过多
- 页面状态管理过于复杂
- 跨服务结果拼装明显重复

---

## 九、正式运行架构文档设计

新增文档：

- `/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`

### 9.1 文档章节建议

1. 系统概述
2. 服务清单
3. 成功路径
4. 失败闭环路径
5. 关键数据对象流转
6. 服务边界
7. 联调方式

### 9.2 文档风格

- 正式、稳定、偏说明书
- 中英双语术语对照
- 重点强调当前真实运行架构，而不是历史设计

---

## 十、测试与验证策略

### 10.1 界面验证

需要验证：

- 页面能正常打开
- 标签页切换正常
- 服务状态可读取
- 成功路径能跑通
- 失败路径能跑通
- 时序流与关键数据面板能正确展示

### 10.2 文档验证

需要验证：

- 文档中的服务、端口、职责与当前实际运行一致
- 文档与界面中的术语一致
- 文档和代码中的主路径描述一致

---

## 十一、实现建议

### 11.1 第一阶段

先完成：

- Testing Service UI 双标签页改造
- 架构总览页
- 正式运行架构文档

### 11.2 第二阶段

再完成：

- 成功路径联调
- 失败路径联调
- 时序流视图

### 11.3 第三阶段

按需要增强：

- 更细粒度的 payload 对比
- 自动刷新
- 失败定位提示

---

## 十二、开放问题

当前设计中唯一明确保留的工程开放点是：

- 系统联调页面是否需要后续新增一个轻量聚合接口，取决于前端直接复用现有接口后是否足够清晰

本阶段先不把它作为前置条件。

---

## 结论

推荐实施方案：

- 在 `Testing Service` 下新增双标签页工作台
- 标签页一：`架构总览（Architecture Overview）`
- 标签页二：`系统联调（System Integration Console）`
- 同时新增正式运行架构文档：
  - `/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`

该方案兼顾：

- 系统架构展示
- 成功/失败路径联调
- 中英双语可读性
- 对现有代码结构的兼容性
