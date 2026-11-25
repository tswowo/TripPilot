# TripPilot — AI Agent 工程与督学工作区指南

本仓库是面向中国互联网大厂 AI Agent / 大模型应用研发岗位打造的实战项目：**《TripPilot —— 基于 MCP + Skills 双生态、pgvector 混合召回与多智能体校验的智能旅行规划引擎》**。

## 必读文档导航（每次新会话请先按需读取）

| 文档 | 用途 |
| --- | --- |
| [docs/STUDY_TRACKER.md](docs/STUDY_TRACKER.md) | **每日 AI 督学总控活文档**：当前天数 `current_day`、P0/P1 任务表、验收口试题、打卡记录与错题本 |
| [docs/ARCHITECTURE_AND_SPEC.md](docs/ARCHITECTURE_AND_SPEC.md) | **系统架构与技术规范**：端到端业务流、美团 Skill + 高德 MCP 桥接、pgvector 混合召回设计、多智能体验收规则 |
| [docs/DEERFLOW_CODE_GUIDE.md](docs/DEERFLOW_CODE_GUIDE.md) | **DeerFlow 源码精读参考**：`f:\deer-flow` 五大核心模块对照解析与离线 `pytest` 命令 |
| [docs/THIRD_PARTY_INTEGRATION.md](docs/THIRD_PARTY_INTEGRATION.md) | **第三方接入手册**：高德 MCP 15 工具契约、美团 Skill 两条链路与响应信封、Markdown 收据抽取正则、版本同步方式与未验证风险清单 |

## 技术栈铁律

- **语言与网关**：Python 3.12（`uv` 管理）+ FastAPI（标准 SSE 多事件流 + 客户端断连 `CancelledError` 传播）。
- **编排与护栏**：`LangChain` + `LangGraph`（`StateGraph` + `AsyncPostgresSaver` Checkpointer + `interrupt` HITL）+ 自研洋葱式护栏中间件（错误结构化自愈、超长输出落盘裁剪、双指纹死循环熔断、悬空 `tool_call` 修复、Skill/MCP 渐进式按需暴露）。
- **外部工具双生态**：
  - **高德地图官方 MCP**（空间计算、地理编码、地铁/步行通勤耗时、实时天气）；
  - **美团开放平台官方酒旅 Skill**（仓库目录：`src/skills/meituan-travel`；通过环境变量 `MEITUAN_HT_TOKEN` 与 `@meituan-travel/ht-ai` `-o json` 调用）。
- **后端中间件**：
  - **PostgreSQL + pgvector**：攻略文档与向量检索、LangGraph 会话断点快照、路书归档、用户长期出行偏好记忆；启用 `vector` 与 `pg_trgm` 扩展，使用关键词/向量混合召回、RRF 融合和百炼 `gte-rerank` 精排；
  - **不引入 Elasticsearch 与 Redis**：RAG 统一放 PostgreSQL；当前单进程阶段使用进程内限流与异步后台任务，不额外部署缓存或消息队列中间件。

## AI 协作与督学规则

1. **安全红线**：绝不读取打印或将 `.env` 中的 `MEITUAN_HT_TOKEN`、`AMAP_MAPS_API_KEY`、`DEEPSEEK_API_KEY` 硬编码进任何受 Git 跟踪的文件（美团平台会自动作废泄露的 Token）。
2. **教学与编码原则**：用户做本项目的核心目标是**吃透原理并在面试中对答如流**。辅助写代码时，先讲清设计取舍和边界坑点，分模块小步推进并配套 `pytest` 单测，不要一言不发直接包办几千行黑盒代码。
3. **每日闭环**：当用户要求推进日常学习或打卡时，严格遵循 `docs/STUDY_TRACKER.md` 顶部的《AI 督学、口试与熔断重排协议》，验收通过后反向更新 `docs/STUDY_TRACKER.md`。
