# 21 天互联网 AI Agent 实习冲刺总控活文档（`TripPilot` · pgvector 混合召回版）

> [!IMPORTANT]
> **当前状态看板（供 AI 每次会话读取与反向更新）**
> - **目标岗位**：主投 **AI Agent / 大模型应用开发实习生**（美团/携程/阿里高德飞猪百炼/字节生活服务与豆包/小红书/腾讯/快手），辅投 **Java 后端开发实习生（AI 应用方向）**。
> - **死守底线**：**Day 7（2026-12-01）必出简历并集中海投**；21 天内拿到实习 Offer。
> - **当前进度**：`current_day: Day 1` | `phase: Week 1 TripPilot 项目冲刺` | `status: 待启动`
> - **累计投递**：`0` 家 | **约面**：`0` 家 | **Offer**：`0` 个
> - **关联文档**：[系统架构说明书](ARCHITECTURE_AND_SPEC.md) · [DeerFlow 源码精读指南](DEERFLOW_CODE_GUIDE.md)

---

## 🤖 AI 督学、口试与熔断重排协议（写给每天接手本文件的 AI）

每次用户唤醒你并让你推进今日学习或开发时，请严格执行以下 **5 条纪律**：
1. **开场抽查（2 分钟）**：读取顶部 `current_day`；若非 Day 1，先拿前一日的 `🎯 AI 验收口试题` 追问 1～2 题，答出才算真正掌握。
2. **严守 P0/P1 防烂尾边界**：每天先死保 **`[P0 必做]`**（4～5 小时可交付、直接支撑简历指标）；**`[P1 选做]`** 有余力再做，做不完直接顺延至 Week 2，绝不拖延次日任务。
3. **无运行验证不打钩**：代码必须有 `pytest` 单测通过或真实运行/评测日志，口试题必须由用户自己复述通过，才可将 `[ ]` 改为 `[x]`。
4. **进度熔断重排规则**：若某日 P0 未完成，次日自动砍掉所有 P1 优先补齐 P0；若累计延误超 1 天，将外围加分项移至 Week 2 边投边补，**死守 Day 7 准时出简历开投**。
5. **每日结束反向更新本文件**：勾选 `[x]`、在 `📝 打卡与复盘` 填入实际耗时与实测数据、递增 `current_day`、把暴露的知识盲区记入底部 `面试错题本`。

---

## 一、以终为始：你的简历项目目标模板（写代码就是为了填满 `[待测]`）

> **TripPilot —— 基于 MCP + Skills 双生态与多智能体校验的智能旅行规划引擎**
> `Python` · `FastAPI (SSE)` · `LangChain / LangGraph` · `PostgreSQL` · `pgvector`
> - **MCP + Skills 渐进式工具治理**：集成高德官方 LBS MCP（15 个工具）与美团酒旅官方 Skill（酒店房价/景区余票/高铁车次），设计跨源地址坐标桥接层；参考字节 DeerFlow 实现**技能按需激活（Progressive Disclosure）**与洋葱式护栏链（异常结构化自愈、超长结果落盘裁剪、双指纹死循环熔断、悬空 `tool_call` 修复），单轮工具定义 Token 开销降低 `[待测%]`。
> - **pgvector 混合召回与 Agentic RAG**：基于 PostgreSQL 构建城市避坑与预约规则库，结合 **`pg_trgm` 关键词召回 + pgvector HNSW 向量召回**、RRF 倒数秩融合与百炼 `gte-rerank` 精排，辅以攻略目录一句话摘要与按需精读，30 题评测集 `Recall@5` 由单路向量的 `[基线%]` 提升至 `[优化后%]`。
> - **上下文工程与轻量中间件治理**：固定静态 System Prompt 头部保护前缀缓存，结合无损分段摘要压缩，10 轮连续改行程实测 DeepSeek `prompt_cache_hit_tokens` 命中率由 `[基线%]` 提升至 `[优化后%]`；当前单进程阶段使用进程内令牌桶限流与 `asyncio` 后台任务，不引入 Redis。
> - **LangGraph 多智能体并行与客观验收闭环**：基于 `StateGraph` + `AsyncPostgresSaver` 并发调度“酒旅票务”、“路网天气”、“避坑攻略”三个上下文隔离的子智能体；设计确定性 `ItineraryValidator` 核查工具收据（防编造车次/酒店）、地铁口步行距离、每日通勤+游玩总时长与总预算硬约束，违规自动带结构化冲突原因打回重排，行程一次合规率由 `[基线%]` 提升至 `[优化后%]`。

---

## 二、Day 1 开工前环境清单（20 分钟）

- [ ] 复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`AMAP_MAPS_API_KEY`（高德 Web 服务 Key）、`MEITUAN_HT_TOKEN`（美团个人开发者 Token，变量名以官方 `SKILL.md` 为准）。
- [ ] 将美团 AI Hub 下载的「美团酒旅 Skill」解压放入 `src/skills/meituan-travel/`（确认敏感凭证走 `.env` 环境变量，不硬编码）。
- [ ] 启动本地或 Docker 的 **PostgreSQL**，启用 `vector` 与 `pg_trgm` 扩展；不启动 Elasticsearch 与 Redis。

---

## 三、Week 1：7 天简历冲刺每日任务（Day 1 ～ Day 7）

### Day 1（2026-11-25）：接入高德 MCP + 美团酒旅 Skill + 四大工具护栏
- **源码精读（1.5h，在 `f:\deer-flow` 查看）**：
  - [ ] `[P0]` 读 `tool_error_handling_middleware.py`（`L94-271`，错误自愈 + `SKILL.md` 读取打标）、`tool_output_budget_middleware.py`（`L532-684`）、`loop_detection_middleware.py`（`L678-939`）、`dangling_tool_call_middleware.py`（`L385-526`）
  - [ ] `[P0]` 在 `f:\deer-flow\backend` 跑通单测：`uv run pytest tests/test_tool_error_handling_middleware.py tests/test_loop_detection_middleware.py tests/test_dangling_tool_call_middleware.py -q`
- **项目编码（4h，在 `F:\TripPilot`）**：
  - [ ] `[P0]` 用 `uv init` 初始化 `pyproject.toml` 与 `src/trippilot` 骨架，安装核心依赖。
  - [ ] `[P0]` 调通 **高德 MCP**（`langchain-mcp-adapters`）与 **美团酒旅 Skill**（解析 `SKILL.md` 并将底层查询脚本封装为结构化 Python Tool），写好美团地址 → 高德 `maps_geo` 的坐标桥接函数。
  - [ ] `[P0]` 实现 4 个护栏中间件并配离线 `pytest` 单测：异常结构化自愈、超 4KB 输出落盘截断、双指纹死循环熔断、悬空 `tool_call` 修复。
  - [ ] `[P1]` 为 `maps_geo`、路线规划、天气增加单进程 TTL 缓存（仅当前实例有效，不引入 Redis）。
- **算法恢复（40min）**：
  - [ ] `[P0]` LC 146 LRU 缓存、LC 206 反转链表
- **🎯 AI 验收口试题**：
  1. 高德 MCP 和美团 `SKILL.md` 技能包在协议与上下文加载方式上有什么核心区别？
  2. 为什么死循环检测必须同时哈希“工具调用参数”和“工具返回结果”？
- **📝 打卡与复盘**：`（待 AI 填写）`

### Day 2（2026-11-26）：Context Engineering + 技能按需激活 + Prefix Cache 实测
- **源码精读（1.5h）**：
  - [ ] `[P0]` 读 `dynamic_context_middleware.py`（`L505-618`）、`summarization_middleware.py`（`L978-1081`）、`deferred_tool_filter_middleware.py`（`L32-162`）
  - [ ] `[P0]` 跑通单测：`uv run pytest tests/test_dynamic_context_middleware.py tests/test_summarization_middleware.py tests/test_deferred_filter_middleware.py -q`
- **项目编码（4h）**：
  - [ ] `[P0]` 实现上下文分层：**静态 System Prompt 锁死头部**；动态日期、用户画像、攻略目录走后置瞬时消息注入；长对话按窗口比例触发无损摘要（严格对齐 `assistant(tool_calls)` 与 `tool` 边界）。
  - [ ] `[P0]` 实现 **Skill & 工具按需暴露**：平时只给美团 Skill 一行简介与基础路由工具，激活后才挂载详细 Schema 与 `SKILL.md` 正文。
  - [ ] `[P0]` **实测取数**：跑 10 轮连续改行程实验，记录单轮工具定义 Token 降幅 % 与 DeepSeek `prompt_cache_hit_tokens` 命中率提升 %！
- **算法恢复（40min）**：
  - [ ] `[P0]` LC 3 无重复字符的最长子串、LC 20 有效的括号
- **🎯 AI 验收口试题**：
  1. 为什么动态时间戳或摘要直接拼进开头 System Prompt 会破坏大模型 Prefix KV Cache？
  2. 压缩历史消息保留最近 K 条时，如果切分点正好是一条 `role="tool"` 消息会发生什么？怎么解决？
- **📝 打卡与复盘**：`（待 AI 填写，记录 Token 降幅 % 与 Cache 命中率 %）`

### Day 3（2026-11-27）：PostgreSQL/pgvector 混合召回（pg_trgm + Vector）+ Agentic 精读
- **源码精读（1.5h）**：
  - [ ] `[P0]` 读 `knowledge/contract.py`（`L13-122`）、`projects/tools.py`（`L113-171`）
  - [ ] `[P0]` 跑通单测：`uv run pytest tests/test_knowledge_contract.py tests/test_project_document_tools.py -q`
- **项目编码（4h）**：
  - [ ] `[P0]` 整理 20～30 篇热门城市真实预约放票规则与防踩雷攻略 Markdown（现有 30 篇社区攻略可作基线，仍需补充可溯源规则条目），启用 `vector`/`pg_trgm`，建好 **`trippilot_guides` 表**（`city/category` 过滤索引 + `gin_trgm_ops` 关键词索引 + `vector_cosine_ops` HNSW 向量索引 + `summary` 目录摘要），复用百炼 Embedding 批量限流退避逻辑入库。
  - [ ] `[P0]` 实现 **城市 Pre-filter + pg_trgm 关键词路 + pgvector 向量路 + RRF 融合 + 百炼 `gte-rerank` 精排**，外加 `read_guide_section` 按需精读全文与确定性 `[source_id]` 溯源。
- **算法恢复（40min）**：
  - [ ] `[P0]` LC 15 三数之和、LC 56 合并区间（对应行程时间段冲突合并！）
- **🎯 AI 验收口试题**：
  1. 中文场景为什么可以用 `pg_trgm` 做关键词召回？它与 PostgreSQL 原生全文检索、pgvector 语义召回各自适合什么查询？
  2. 为什么要先按 `city` 做 Pre-filter 再跑向量检索？pgvector HNSW 与精确最近邻查询有什么区别？RRF 为什么能融合不同量纲的召回分数？
- **📝 打卡与复盘**：`（待 AI 填写）`

### Day 4（2026-11-28）：LangGraph 多智能体并行 + 时空预算客观验收 + 异步长期记忆
- **源码精读（1.5h）**：
  - [ ] `[P0]` 读 `subagents/capacity.py`（`L37-159`）、`acceptance_checks.py`（`L2155-2216`）、`memory_middleware.py`（`L160-271`）
  - [ ] `[P0]` 跑通单测：`uv run pytest tests/test_subagent_capacity.py tests/test_acceptance_checks.py tests/test_memory_queue.py -q`
- **项目编码（4.5h）**：
  - [ ] `[P0]` 基于 **LangGraph `StateGraph` + `AsyncPostgresSaver`** 搭建并发主图：拆单并发调度 **票务酒旅员（美团 Skill）**、**路网天气员（高德 MCP）**、**避坑攻略员（pgvector 混合 RAG）**（上下文相互隔离）。
  - [ ] `[P0]` 落地核心杀手锏 **`ItineraryValidator`（确定性代码验收节点）**：核查 ① 车次/酒店/票价真实存在于 `tool_receipts` 收据；② 地铁口步行距离、雨天避爬山、单日总时长 `≤9h`；③ 总花费 `≤预算`。失败生成结构化冲突列表沿条件边打回重排！支持 `interrupt` 人工确认微调。
  - [ ] `[P0]` **`asyncio` 后台任务 → PostgreSQL 长期记忆**：会话后异步提炼用户忌口/预算/作息偏好（过滤中间 `tool_calls` 噪音），下次自动召回；当前单进程不引入消息队列。
- **算法恢复（40min）**：
  - [ ] `[P0]` LC 207 课程表（拓扑排序）、LC 102 二叉树层序遍历
- **🎯 AI 验收口试题**：
  1. 为什么行程验收要用确定性 Python 代码核查 `tool_receipts`，而不是让另一个大模型当裁判？
  2. 子 Agent 为什么要和主 Agent 做上下文隔离（不传完整父历史消息）？
- **📝 打卡与复盘**：`（待 AI 填写）`

### Day 5（2026-11-29）：FastAPI SSE + 单实例限流 + 30 题黄金评测集跑分
- **项目编码（5h）**：
  - [ ] `[P0]` 封装 FastAPI `/api/plan/stream`：标准 SSE 多事件推送、心跳保活、客户端断连触发 `asyncio.CancelledError` 优雅取消 + 单实例进程内令牌桶限流 + 极简演示页（Streamlit 或一键生成高德路线打点 HTML）。
  - [ ] `[P0]` 构建 **30 题评测集**（10 题预约/避坑检索 + 10 题跨城高铁酒店预算规划 + 10 题雨天/闭馆/特种兵时长压测），运行 `eval/run_eval.py` 跑出 **4 组硬核消融数据**（工具 Token 降幅、Prefix Cache 命中率、单路向量 vs pg_trgm+向量+Rerank 的 `Recall@5`、经 `ItineraryValidator` 重排前后的行程合规率），填满上面简历模板！
  - [ ] `[P1]` 接入 Langfuse 截取一张完整多智能体调用链火焰图放入 `docs/`。
- **AI 工程手写题（1h）**：
  - [ ] `[P0]` 手写 **令牌桶限流器** + 30 行标准 **async ReAct 循环**
- **🎯 AI 验收口试题**：
  1. SSE 和 WebSocket 有什么区别？Nginx 反代 SSE 必须配什么参数？前端点“停止”后端怎么防白烧 Token？
  2. 讲讲你这 30 道评测题是怎么设计的，为什么能证明每个模块不是过度设计？
- **📝 打卡与复盘**：`（待 AI 填写，完整记录 4 组实测评测数据）`

### Day 6（2026-11-30）：GitHub 仓库打磨 + 一页纸简历定稿
- [ ] `[P0]` 推送 `TripPilot` 到 GitHub，写好高质量 `README.md`（Mermaid 架构图 + pgvector 混合召回图 + 评测数据对比表；**严格检查无任何密钥泄露**）。
- [ ] `[P0]` 定稿 **一页纸 PDF 简历**（主版投 AI Agent 研发实习；辅版结合 `TS-CloudDemo` 投 Java AI 后端实习；移除苍穹外卖/黑马点评）。
- [ ] `[P1]` 选做：给 `TS-CloudDemo` 网关加一条到 `TripPilot` 的路由，或给 `bytedance/deer-flow` 提 Windows 启动脚本优化 PR。
- **📝 打卡与复盘**：`（待 AI 填写）`

### Day 7（2026-12-01）：集中海投 50+ 家 + 首轮全真模拟面
- [ ] `[P0]` 集中投递 **美团、携程、阿里（高德/飞猪/通义/淘天）、字节（抖音生活服务/豆包/扣子）、小红书、腾讯、快手** 及 AI 独角兽日常实习，录入底部漏斗表。
- [ ] `[P0]` 首轮 45 分钟全真模拟面试（AI 扮演美团/阿里一面面试官深挖 `TripPilot`），盲区记入错题本。
- [ ] `[P0]` 算法恢复：LC 236 二叉树最近公共祖先、LC 42 接雨水
- **📝 打卡与复盘**：`（待 AI 填写）`

---

## 四、Week 2 ～ Week 3：边投边面冲刺表（Day 8 ～ Day 21）

| 天数 | 算法题（Hot100 + 工程题） | 八股专项（脱稿讲原理 + 结合 `TripPilot` 举例） | 投递与面试动作 | 状态 |
| --- | --- | --- | --- | --- |
| **Day 8** | LC 21 合并有序链表、LC 142 环形链表 II | **Agent 八股 ①**：LangGraph vs LangChain、Function Calling vs MCP vs `SKILL.md` | 补投 15+ 家，跟进已读未回 | `[ ]` |
| **Day 9** | LC 200 岛屿数量、LC 33 搜索旋转排序数组 | **pgvector 与 RAG 八股**：pg_trgm 相似度、HNSW 图索引、余弦距离、RRF 融合、Rerank 与 Eval 指标 | 模拟面：pgvector 混合召回与幻觉治理 | `[ ]` |
| **Day 10** | LC 53 最大子数组和、LC 322 零钱兑换 | **Agent 八股 ③**：Prefix KV Cache 原理、无损压缩边界、异步长期 Memory、Workflow vs Agent | 约面排期 + 补齐 Week 1 遗留 P1 | `[ ]` |
| **Day 11** | LC 300 最长递增子序列、LC 72 编辑距离 | **网络与流式八股**：SSE vs WebSocket、Nginx `proxy_buffering off`、断连取消释放、TCP/HTTP2 | 模拟面：流式网关与高可用 | `[ ]` |
| **Day 12** | LC 215 数组第 K 大、LC 347 前 K 高频元素 | **数据库八股（MySQL + PG）**：B+ 树、最左前缀与回表、Redo/Undo Log、MVCC ReadView、Next-Key Lock | 复盘真实一面真题 | `[ ]` |
| **Day 13** | LC 25 K 个一组翻转链表、LC 739 每日温度 | **PostgreSQL/pgvector 八股**：MVCC、索引选择、HNSW 参数、过滤与召回、事务与连接池 | 微调简历话术 | `[ ]` |
| **Day 14** | 工程手写复测：LRU + 令牌桶 + 异步并发聚合 | **并发与异步八股**：Python `asyncio` 阻塞 IO 陷阱；Java 线程池 7 参数与 `CompletableFuture` | **Week 2 复盘**：目标 3～5 家在面 | `[ ]` |
| **Day 15–21** | 每日 2 题错题限时 20 分钟白板手撕 | 二面/三面系统设计题演练（如：“设计日活百万的酒旅规划 Agent 网关与降本容灾方案”） | 密集二面/HR 面 + 谈 Offer | `[ ]` |

---

## 五、动态追踪区（由 AI 每日反向追加更新）

### 1. 投递与面试漏斗表
| 日期 | 公司 / 部门 | 岗位方向 | 渠道 | 当前进度 | 关键反馈 / 下一步 |
| --- | --- | --- | --- | --- | --- |
| `待填` | `美团-酒旅/平台AI` | `AI Agent 实习生` | `官网/BOSS` | `待投递` | `重点突出美团酒旅 Skill + pgvector 混合召回！` |

### 2. 面试错题与知识盲区本
- *(暂无记录，Day 1 开始累积)*
