# 工业 / 官方资料补充清单（2026-04-16）

说明：面向数据问答、Text-to-SQL、语义层、数据 Copilot、分析 Agent 等方向补充官方或正式产品/开发资料。优先选择厂商官方文档，其次选择正式产品页或官方方案页。

## Microsoft / Fabric / Power BI

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Overview of Copilot in Fabric | Microsoft | https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview | microsoft,fabric,copilot,overview | Fabric 总览页，覆盖多工作负载中的 Copilot 能力，是后续细分文档的入口。 |
| Microsoft Copilot in the Data Warehouse Workload Overview | Microsoft | https://learn.microsoft.com/en-us/fabric/data-warehouse/copilot | microsoft,fabric,data-warehouse,nl2sql | 直接说明 Fabric Warehouse 的自然语言转 SQL、代码补全和智能洞察能力。 |
| Overview of Copilot for Data Science and Data Engineering in Microsoft Fabric | Microsoft | https://learn.microsoft.com/en-us/fabric/data-engineering/copilot-notebooks-overview | microsoft,fabric,data-engineering,notebooks | 说明 Fabric 在 notebook 场景下的 Copilot 设计，对数据工程和分析开发流程很相关。 |
| How to Get Started with Microsoft Copilot in Fabric in the Data Factory Workload | Microsoft | https://learn.microsoft.com/en-us/fabric/data-factory/copilot-fabric-data-factory-get-started | microsoft,fabric,data-factory,natural-language | 展示 Data Factory 中用自然语言生成和解释数据转换步骤的正式操作文档。 |
| Use Copilot with semantic models | Microsoft | https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-semantic-models | microsoft,power-bi,semantic-model,copilot | 直接关联 Power BI 语义模型与 Copilot，是 NLQ grounded on semantic model 的关键资料。 |
| Prepare Your Data for AI - AI Data Schemas | Microsoft | https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-data-schema | microsoft,power-bi,ai-data-schema,grounding | 解释如何为 Copilot 准备 AI data schema，属于提升问答准确率的核心开发文档。 |

## Google / BigQuery / Vertex AI

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| BigQuery overview | Google Cloud | https://cloud.google.com/bigquery/docs/introduction | google,bigquery,overview,gemini | BigQuery 总览已把 Gemini in BigQuery、data insights 等能力纳入统一入口。 |
| Analyze with BigQuery data canvas | Google Cloud | https://docs.cloud.google.com/bigquery/docs/data-canvas | google,bigquery,data-canvas,natural-language | Data canvas 是 BigQuery 中最直接的自然语言分析工作流文档。 |
| Generate data insights in BigQuery | Google Cloud | https://cloud.google.com/bigquery/docs/data-insights | google,bigquery,data-insights,auto-analysis | 说明如何基于表元数据自动生成问题与 SQL，和自助分析助手高度相关。 |
| Generate structured data by using the AI.GENERATE_TABLE function | Google Cloud | https://cloud.google.com/bigquery/docs/generate-table | google,bigquery,ai.generate_table,structured-output | 展示 BigQuery 内置 AI SQL 函数如何把模型输出约束为结构化结果。 |
| The AI.GENERATE_TABLE function | Google Cloud | https://cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-generate-table | google,bigquery,sql-reference,ai-function | 这是 `AI.GENERATE_TABLE` 的正式语法参考，适合做实现和 prompt-to-table 约束。 |
| Vertex AI Agent Builder documentation | Google Cloud | https://cloud.google.com/agent-builder | google,vertex-ai,agent-builder,official-docs | Vertex Agent Builder 是 Google 官方 agent 平台入口，可用于数据问答型 agent 方案对照。 |

## Snowflake / Cortex Analyst

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Cortex Analyst | Snowflake | https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst | snowflake,cortex-analyst,text-to-sql | Snowflake 官方的 Cortex Analyst 总览，直接对应结构化数据问答与 Text-to-SQL。 |
| Cortex Analyst REST API | Snowflake | https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/rest-api | snowflake,cortex-analyst,rest-api | 给出 Analyst 的正式 API 入口，适合集成到应用、聊天界面或 agent 中。 |
| Cortex Analyst semantic model specification | Snowflake | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec | snowflake,semantic-model,yaml-spec | 语义模型规格是 Cortex Analyst 准确率和可控性的核心 grounding 载体。 |
| Overview of semantic views | Snowflake | https://docs.snowflake.com/user-guide/views-semantic/overview | snowflake,semantic-view,semantic-layer | Snowflake 官方解释 semantic view 如何承载业务语义并被 Cortex Analyst 使用。 |
| Semantic View Editor | Snowflake | https://docs.snowflake.com/en/user-guide/views-semantic/editor | snowflake,semantic-view,editor | 说明如何在 Snowsight 中编辑语义视图，适合构建可维护的语义层。 |
| Semantic View Autopilot | Snowflake | https://docs.snowflake.com/en/user-guide/views-semantic/autopilot | snowflake,semantic-view,autopilot | 展示 Snowflake 用 AI 辅助生成语义层，是很直接的工业实践资料。 |
| CREATE SEMANTIC VIEW | Snowflake | https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view | snowflake,sql,semantic-view,ddl | 提供 semantic view 的正式 DDL 语法，适合集成和工程化实现。 |
| DESCRIBE SEMANTIC VIEW | Snowflake | https://docs.snowflake.com/en/sql-reference/sql/desc-semantic-view | snowflake,sql,semantic-view,introspection | 描述如何对语义视图做结构化 introspection，适合自动化工具和 agent 读取。 |

## Databricks / Genie / AI-BI

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| What is an AI/BI Genie space | Databricks | https://docs.databricks.com/en/genie/index.html | databricks,genie,ai-bi,natural-language | Genie 是 Databricks 面向业务用户的 NLQ 主入口，和本任务高度对齐。 |
| Set up and manage an AI/BI Genie space | Databricks | https://docs.databricks.com/en/genie/set-up.html | databricks,genie,setup,governance | 该文档给出 Genie 空间配置、权限、数据源与知识上下文管理方式。 |
| Use the Genie API to integrate Genie into your applications | Databricks | https://docs.databricks.com/gcp/en/genie/conversation-api | databricks,genie,api,application-integration | 官方 Conversation API 文档可直接支撑二次开发和外部应用嵌入。 |
| Add a Genie space resource to a Databricks app | Databricks | https://docs.databricks.com/aws/en/dev-tools/databricks-apps/genie | databricks,genie,apps,resource | 说明如何把 Genie 作为应用资源接入，是产品化集成的正式说明。 |
| Genie spaces with dashboards | Databricks | https://docs.databricks.com/gcp/en/dashboards/genie-spaces | databricks,genie,dashboard,ask-genie | 展示 Genie 与 dashboard 的组合方式，体现自然语言分析和 BI 的联动。 |
| Connect agents to structured data | Databricks | https://docs.databricks.com/aws/en/generative-ai/agent-framework/structured-retrieval-tools | databricks,agent,mcp,structured-data | 官方建议 agent 查询结构化数据时优先用 Genie space，对 agent 场景非常关键。 |

## Oracle / Select AI / APEX

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| About Select AI | Oracle | https://docs.oracle.com/iaas/autonomous-database-serverless/doc/select-ai-about.html | oracle,select-ai,nl2sql | Oracle Select AI 的正式概述，覆盖自然语言转 SQL、RAG 和聊天等模式。 |
| Select AI Concepts | Oracle | https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/select-ai-concepts.html | oracle,select-ai,concepts,rag | 从概念层说明 Select AI 的能力边界、RAG 和 NL2SQL 机制。 |
| Select AI User's Guide | Oracle | https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/oracle-database-select-ai-users-guide.pdf | oracle,select-ai,users-guide,pdf | 用户手册是 Oracle 在 Select AI 方向最系统的正式资料之一。 |
| Configure Select AI for Your Database | Oracle | https://docs.oracle.com/en/database/oracle/agent-factory/25.3/paias/select-ai.html | oracle,select-ai,configuration,agent-factory | 说明在 Agent Builder 中为数据库配置 Select AI profile，是工程接入关键文档。 |
| Build an Agentic, High-Fidelity, Conversational AI Framework with Select AI and Oracle APEX | Oracle | https://docs.oracle.com/en/solutions/select-ai-apex-framework/index.html | oracle,apex,select-ai,solution | Oracle 官方方案页，展示 Select AI 与 APEX 组合成对话式分析框架的完整路径。 |

## IBM / watsonx / Netezza / Db2

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| IBM watsonx.data | IBM | https://www.ibm.com/products/watsonx-data | ibm,watsonx,data,lakehouse | watsonx.data 是 IBM 在数据底座和 AI/BI 场景的核心产品入口。 |
| IBM watsonx.data intelligence | IBM | https://www.ibm.com/products/watsonx-data-intelligence | ibm,watsonx,data-intelligence,metadata | 强调元数据、治理和自然语言访问，对数据问答 grounding 很相关。 |
| watsonx BI | IBM | https://www.ibm.com/products/watsonx-bi | ibm,watsonx,bi,insight-agent | 这是 IBM 明确面向业务洞察 agent 的正式产品页，相关性很高。 |
| AI-powered IBM Netezza Database Assistant general availability | IBM | https://www.ibm.com/new/announcements/ai-powered-ibm-netezza-database-assistant-general-availabilty | ibm,netezza,database-assistant,natural-language | 正式发布页明确说明用自然语言与数据库实例交互的产品形态。 |
| IBM Db2 watsonx Database Assistant | IBM | https://www.ibm.com/products/db2/database-assistant-watsonx | ibm,db2,database-assistant,watsonx | Db2 的官方数据库助手页，补足 IBM 在关系数据库侧的 AI 查询入口。 |

## SAP / Joule / Analytics Cloud

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| What is Joule? | SAP | https://help.sap.com/docs/joule/serviceguide/what-is-joule | sap,joule,copilot,overview | Joule 官方定义页，是 SAP 整体 AI copilot 体系的总入口。 |
| Data Protection and Privacy | SAP | https://help.sap.com/docs/joule/serviceguide/data-protection-and-privacy | sap,joule,privacy,governance | 企业采用 SAP Joule 时最关键的官方合规和隐私说明。 |
| Integrate Joule | SAP | https://help.sap.com/docs/start/sap-start/integrate-joule | sap,joule,integration,sap-start | 给出 Joule 集成到 SAP Start 的正式路径，属于产品接入资料。 |
| Exploring Your Data with Just Ask | SAP | https://help.sap.com/docs/SAP_ANALYTICS_CLOUD/00f68c2e08b941f081002fd3691d86a7/95dbe296761940c2bf4e18d54a20f3df.html | sap,analytics-cloud,just-ask,natural-language-query | SAC 中 Just Ask 是 SAP 官方的自然语言数据问答能力。 |
| Search to Insight | SAP | https://help.sap.com/docs/SAP_ANALYTICS_CLOUD/00f68c2e08b941f081002fd3691d86a7/e1b4914ffbc8438eb1aefccf70362d39.html | sap,analytics-cloud,search-to-insight,nlq | Search to Insight 是 SAP 在 NLQ 历史能力上的正式文档，适合补全产品谱系。 |

## Salesforce / Tableau

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Tableau Pulse | Salesforce / Tableau | https://www.tableau.com/metrics | salesforce,tableau,pulse,ai-insights | Tableau Pulse 是 Salesforce 当前最核心的 AI 驱动分析体验之一。 |
| Tableau Pulse Datasheet | Salesforce / Tableau | https://www.tableau.com/learn/whitepapers/tableau-pulse-datasheet | salesforce,tableau,pulse,datasheet | 数据表式资料更适合快速抽取产品能力、定位和可用性信息。 |
| Build Views Automatically with Ask Data | Salesforce / Tableau | https://help.tableau.com/current/online/en-us/ask_data.htm | salesforce,tableau,ask-data,natural-language-query | Ask Data 是 Tableau 的经典 NLQ 能力，适合做产品演进和能力对照。 |
| Enable or Disable Ask Data for a Site | Salesforce / Tableau | https://help.tableau.com/current/pro/desktop/en-us/ask_data_enable.htm | salesforce,tableau,ask-data,admin | 展示 Ask Data 的站点级开关和治理方式，适合企业部署视角。 |
| Optimize Data for Ask Data | Salesforce / Tableau | https://help.tableau.com/current/online/en-us/ask_data_optimize.htm | salesforce,tableau,ask-data,optimization | 这是 Tableau 官方关于提升 NLQ 结果质量的直接资料。 |

## MongoDB / Atlas / SQL / NLQ

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Query with Natural Language | MongoDB | https://www.mongodb.com/docs/atlas/atlas-ui/query-with-natural-language/ | mongodb,atlas,natural-language-query | Atlas 官方 NLQ 文档，直接说明自然语言生成 filter 和 aggregation。 |
| Atlas SQL Interface | MongoDB | https://www.mongodb.com/atlas/sql | mongodb,atlas,sql-interface,analytics | Atlas SQL Interface 是 MongoDB 面向 SQL 工具和 BI 的正式入口。 |
| Query with SQL Interface | MongoDB | https://www.mongodb.com/docs/sql-interface/query-with-sql/ | mongodb,sql-interface,querying,docs | 提供 SQL Interface 的正式使用说明，适合集成 BI 或 SQL 驱动流程。 |
| MongoDB SQL Interface Overview | MongoDB | https://www.mongodb.com/docs/atlas/data-federation/query/connect-with-sql-overview/ | mongodb,mongosql,sql-overview,data-federation | 展示 MongoSQL 与 Atlas / EA 的整体定位，适合补产品理解层。 |
| Natural Language Charts | MongoDB | https://www.mongodb.com/docs/charts/chart-type-reference/natural-language-charts/ | mongodb,charts,natural-language,visualization | 展示 MongoDB 用自然语言生成图表的正式能力。 |
| AI and Data Usage Information | MongoDB | https://www.mongodb.com/docs/charts/ai-and-data-usage-information/ | mongodb,charts,ai,privacy | 对生成式 AI 的数据使用边界解释得很清楚，适合合规侧引用。 |

## Elastic / Search AI / Agent Builder

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Elastic Docs | Elastic | https://www.elastic.co/docs | elastic,docs,platform,official | Elastic 新版官方文档总入口，可进一步延展到搜索、分析、AI 能力。 |
| AI Assistant for Elastic Observability and Elasticsearch | Elastic | https://www.elastic.co/docs/solutions/observability/observability-ai-assistant/ | elastic,ai-assistant,observability,search | 这是 Elastic 官方 AI Assistant 的详细说明页，覆盖聊天、查询构建和可视化。 |
| AI Assistant for Elastic Observability and Elasticsearch | Elastic | https://www.elastic.co/docs/solutions/search/ai-assistant | elastic,ai-assistant,elasticsearch,search | Search 方案视角下的同类文档，更贴近搜索/数据探索使用场景。 |
| Elastic Agent Builder | Elastic | https://www.elastic.co/docs/explore-analyze/ai-features/elastic-agent-builder | elastic,agent-builder,ai-agent,natural-language | Agent Builder 说明 Elastic 如何用官方平台构建对话式数据 agent。 |
| AI agent skills for Elastic | Elastic | https://www.elastic.co/docs/explore-analyze/ai-features/agent-skills | elastic,agent-skills,official-skills,open-source | 官方 skill 包文档体现 Elastic 对 agent 工程化集成的正式支持。 |
| Query DSL | Elastic | https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html/ | elastic,query-dsl,query-language,reference | Query DSL 是 Elastic 查询底座，对任何 NLQ-to-query 场景都很关键。 |

## Open Source / 开源官方文档

| 标题 | 机构 | 链接 | 标签 | relevance |
| --- | --- | --- | --- | --- |
| Using AI with Superset | Apache Superset | https://superset.apache.org/user-docs/using-superset/using-ai-with-superset/ | opensource,superset,mcp,ai-assistant | Superset 官方已给出通过 MCP 连接 AI assistant 的正式用户文档。 |
| Apache Superset Official Site | Apache Superset | https://superset.apache.org/ | opensource,superset,semantic-layer,bi | Superset 首页明确强调 semantic layer、SQL IDE、dashboard 等核心能力。 |
| Introduction | Cube | https://docs.cube.dev/ | opensource,cube,semantic-layer,agentic-analytics | Cube 文档首页直接把 semantic layer 与 agentic analytics 连接起来。 |
| Semantic Layer Sync | Cube | https://cube.dev/docs/product/apis-integrations/semantic-layer-sync | opensource,cube,semantic-layer,bi-sync | 这是 Cube 将语义层同步到 BI 工具的正式文档，适合对照工业语义层设计。 |
| LangChain | Cube | https://cube.dev/docs/product/configuration/visualization-tools/langchain | opensource,cube,langchain,conversational-analytics | 官方给出用 LangChain 构建基于语义层对话接口的路径。 |
| AI agents | Lightdash | https://docs.lightdash.com/guides/ai-analyst | opensource,lightdash,ai-analyst,natural-language | Lightdash 官方 AI analyst 文档和语义指标层关系非常紧密。 |
| Welcome to Lightdash | Lightdash | https://docs.lightdash.com/ | opensource,lightdash,metrics,self-serve-analytics | Lightdash 文档总入口，适合作为开源自助分析与指标层资料补充。 |
| Preset Chatbot Overview | Preset | https://docs.preset.io/docs/preset-chatbot | opensource-adjacent,preset,chatbot,natural-language | Preset 官方 chatbot 文档展示了 Superset 商业发行版中的内建分析助手。 |
| AI Assisted SQL Querying | Preset | https://docs.preset.io/docs/ai-assisted-sql-querying | opensource-adjacent,preset,sql-generation,nl2sql | 直接说明用自然语言生成 SQL 的产品能力和支持数据库范围。 |
| Text-to-SQL Guide (Query Engine + Retriever) | LlamaIndex | https://docs.llamaindex.ai/en/stable/examples/index_structs/struct_indices/SQLIndexDemo/ | opensource,llamaindex,text-to-sql,query-engine | LlamaIndex 官方 Text-to-SQL 示例是开源实现路线里的高相关参考。 |
