# 第三方接入能力清单

判定原则：**`src/skills/` 只放本项目运行时加载的 Skill；参考样本统一放 `references/skill-archives/`，不进入运行路径。**

```text
src/skills/
├── meituan-travel/       # 唯一运行时 Skill
├── README.md
└── sources.json          # 来源、版本、采用/归档状态

references/skill-archives/
├── amap-cli-skill/
├── meituan-travel-skillhub-1.0.7/
└── meituan-travel-source/
    └── channel.json      # 官方包附带但当前适配器不使用
```

## 四种接入形态（按本项目实际用法）

| 形态 | 实例 | 本项目怎么用 | 要不要下载包 |
| --- | --- | --- | --- |
| **MCP（Streamable HTTP）** | 高德 `https://mcp.amap.com/mcp?key=` | `langchain-mcp-adapters` 直连，工具由 `list_tools` 自动发现 | **不用**，只需 `pip install langchain-mcp-adapters` |
| **Skill 文档 + 官方 CLI** | 美团 `meituan-travel` → `@meituan-travel/ht-ai` | `SKILL.md` 是给模型读的操作手册；实际调用走 `npx ht-ai` 子进程 | 存 `SKILL.md`，CLI 由 npx 按 pin 的版本取 |
| **Skill + CLI + GUI 容器** | 高德 `amap-cli-skill` → `@amap-lbs/amap-gui` | 依赖 Electron 桌面容器，无头环境跑不了 → 仅存档 | 存档 |
| **社区转载的官方 Skill** | `skillhub.cn` 上的 `meituan-travel@1.0.7` | 走 `ht-ai-open` + passport 扫码，`client_id` 硬编码且 `verified=false` → 仅存档 | 存档 |

## 关键区别：`ht-ai` 与 `ht-ai-open` 是两个不同链路

| 包 | 端点 | 鉴权来源 | 适用 |
| --- | --- | --- | --- |
| `@meituan-travel/ht-ai` | `/v1/api/voyage/openapi/query` | 开发者 Token（`MEITUAN_HT_TOKEN`） | **本项目采用**，已实测跑通 |
| `@meituan-travel/ht-ai-open` | `/v1/api/voyage/auth/openapi/query` | 用户 passport 扫码授权 | 面向 C 端 AI 助手，需要真实美团账号登录 |

端点不同、鉴权模型不同，**不要混用**。

## 同步策略

平台方不会通知你 Skill 变了，所以按来源分三种探测方式：

1. **npm 包**：查 `registry.npmjs.org/<pkg>` 的 `dist-tags.latest`。这是权威口径。
2. **Skill 文档**：SkillHub 这类平台有 `latestVersion.version` 与 `changelog` 字段，可直接 GET。
3. **MCP Server**：没有包要同步，但**协议会变**——高德 2026-03-17 下线了 SSE 传输，只留 Streamable HTTP。所以 MCP 的"同步"是盯 [更新日志](https://lbs.amap.com/api/mcp-server/changelog) 和跑一次 `list_tools` 比对工具名集合。

一条命令跑前两种：

```bash
uv run python scripts/check_skill_updates.py
```

版本变了要做三件事：改 `sources.json` 的 `package_latest`、重跑 `tests/test_meituan_contract.py`（它锁的是响应契约，最容易先炸）、diff 新旧 `SKILL.md` 看约束有没有变。

## 为什么不用 `@latest`

`SKILL.md` 里示例都是 `npx pkg@latest`。演示可以，**项目里必须 pin 死版本**：包版本一漂移，`data` 里的 Markdown 排版就可能变，收据抽取器的正则当场失效。本项目在 `sources.json` 里记录 pin 版本，CLI 调用处显式写 `@<version>`。
