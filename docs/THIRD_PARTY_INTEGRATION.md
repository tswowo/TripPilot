# 第三方平台接入手册：高德 MCP · 美团酒旅 Skill

> 本文所有结论均来自 2026-11-25 实测，原始样本固化在 `tests/fixtures/`，离线可复跑。
> 每节区分 **[平台提供]** 与 **[本项目实现]**，避免面试时把 SDK 能力说成自己写的。

## 0. 一览

| | 高德地图 | 美团酒旅 |
| --- | --- | --- |
| 接入形态 | **[平台提供]** 远程 MCP Server（Streamable HTTP） | **[平台提供]** 官方 Skill 文档 + npm CLI |
| 鉴权 | URL query `?key=<AMAP_MAPS_API_KEY>` | 请求头 `Authorization: <MEITUAN_HT_TOKEN>`（无 `Bearer` 前缀） |
| 返回数据 | 结构化 JSON | **一整段 Markdown**，无结构化字段 |
| 实测单次延迟 | < 1s | 15.2 ~ 29.7s（4 次，仅单跳票务；官方称复杂行程 1-2 分钟，未实测） |
| 本项目用途 | 经纬度、通勤耗时、天气、唤端地图链接 | 车次/航班/房价/门票——预算核验的唯一数据源 |

---

## 1. 高德 MCP

### 1.1 接入 [平台提供]

```
https://mcp.amap.com/mcp?key=<AMAP_MAPS_API_KEY>
```

- **只有 Streamable HTTP**。高德已于 **2026-03-17 下线通用级 SSE 传输**，网上大量教程仍写 `https://mcp.amap.com/sse?key=`，照抄必然连接失败。
- 不需要下载任何包。`uv add langchain-mcp-adapters`（实测 0.3.2，底层 `mcp` 1.30.0）即可。
- 另有 `@amap/amap-maps-mcp-server@0.0.8` 走 stdio，但最后发布停在 **2025-04-25** 且要求 Node ≥ 22.14.0，远程 HTTP 方式没有理由用它。
- **[本项目实现]** `src/trippilot/tools/amap_mcp.py` 里通过 `httpx_client_factory` 强制 `trust_env=False`：本机带 `HTTP_PROXY=127.0.0.1:32095` 时，国内服务会被代理截胡返回 400。这条对 `requests`/`httpx` 同样成立（默认 `trust_env=True`）。

### 1.2 15 个工具 [平台提供]

实测 `list_tools()` 返回 15 个（文档首页写"12 大核心"是 2025-03 上线时的旧口径）。

| 类别 | 工具 | 必填参数 | 备注 |
| --- | --- | --- | --- |
| 查询 | `maps_geo` | `address`（`city` 可选） | 地址 → `location` 经纬度 + `adcode` |
| 查询 | `maps_regeocode` | `location` | 坐标 → 行政区划 |
| 查询 | `maps_ip_location` | `ip` | |
| 查询 | `maps_weather` | `city` | 返回 `forecasts`，含未来几天 |
| 查询 | `maps_text_search` | `keywords` | |
| 查询 | `maps_around_search` | `keywords`, `location` | `radius` 可选 |
| 查询 | `maps_search_detail` | `id` | 依赖上一步拿到的 poiid |
| 路径 | `maps_direction_walking` | `origin`, `destination` | ≤100km |
| 路径 | `maps_direction_driving` | `origin`, `destination` | |
| 路径 | `maps_direction_bicycling` | `origin`, `destination` | ≤500km |
| 路径 | `maps_direction_transit_integrated` | `origin`, `destination`, **`city`, `cityd`** | **跨城必须同时传起点城市与终点城市**，上海→南京这类行程漏了取不到结果 |
| 路径 | `maps_distance` | `origin`, `destination` | 支持驾车/步行/球面 |
| 唤端 | `maps_schema_personal_map` | `linelist`（按行程顺序的点位） | **返回高德专属地图链接**，可直接替代自研路线打点页 |
| 唤端 | `maps_schema_navi` | 终点 | 返回唤起高德导航的 URI |
| 唤端 | `maps_schema_take_taxi` | `origin`, `destination` | 返回唤起打车的 URI |

坐标格式统一为字符串 `"经度,纬度"`，GCJ-02。

### 1.3 返回结构与拆包 [本项目实现]

工具返回值统一是 `[{"type":"text","text":"<JSON 字符串>"}]`，`parse_mcp_payload()` 负责拆：

```python
await maps_geo.ainvoke({"address": "上海虹桥火车站", "city": "上海"})
# → {"results": [{"city":"上海市","district":"闵行区","adcode":"310112",
#                  "location":"121.318910,31.193987","level":"公交地铁站点"}]}
```

拆完即可直接做确定性校验，这是高德侧比美团侧省事的地方。

### 1.4 复现

```bash
uv run python scripts/probe_amap_mcp.py
```

拉取工具清单写入 `tests/fixtures/amap_tools.json` / `.args.json`，并实调 `maps_geo` 与 `maps_weather`。

---

## 2. 美团酒旅 Skill

### 2.1 两条链路，别混用

| 包 | 端点 | 鉴权 | 结论 |
| --- | --- | --- | --- |
| `@meituan-travel/ht-ai@0.0.6` | `/v1/api/voyage/openapi/query` | 开发者 Token（`MEITUAN_HT_TOKEN`） | **本项目采用**，已实测跑通 |
| `@meituan-travel/ht-ai-open@0.0.4` | `/v1/api/voyage/auth/openapi/query` | 用户 passport 扫码，`client_id` 硬编码在 Skill 里 | 面向 C 端助手，本项目不用 |

两个包由同一批 npm maintainer 发布，都是官方的，但**端点与鉴权模型完全不同**。`skillhub.cn` 上那份 `meituan-travel@1.0.7` 属于后者，且该站由腾讯运营、条目 `verified=false`、`claim_state=unclaimed`——它只是社区转载的 SKILL.md，不是美团发布渠道。**取 Skill 一律走 `developer.meituan.com` 的 AI Hub。**

### 2.2 调用方式 [平台提供]

**必须走官方 CLI。** 美团 Skill 明文规定"严禁使用 curl、fetch、axios 等方式直接调用 API"。本轮排障期间曾用直连定位故障原因（见 2.3 声明），**运行期代码一律不得直连**。

官方示例形态（`SKILL.md` 的 Setup 与查询节）：

```bash
npx -y @meituan-travel/ht-ai@latest query \
  --query '上海到南京的高铁车次和票价' \
  --origin-query '明天从苏州去上海' --channel meituan-developer --city 南京
```

**[本项目实现]** `src/trippilot/tools/meituan_skill.py` 严格按该形态拼装命令，四条实现约束：

- `subprocess.run([...], shell=False)` 列表传参——因此**不需要** `SKILL.md` 核心流程第 2 步要求的单引号转义（那是拼 shell 字符串才需要的；照搬转义会把 `'\''` 当字面量塞进 query）。
- **成功 JSON 走 stdout，失败 JSON 走 stderr**，且失败时 stdout 完全为空。适配层必须两路都捞，只读 stdout 会把所有失败误判成"无输出"。
- 命令里剔除 `HTTP_PROXY` / `HTTPS_PROXY` / `NODE_USE_ENV_PROXY` 并置 `NO_PROXY=*`：本机调试代理会让请求直接 400。
- 提供 `arun_query()` 包一层 `asyncio.to_thread`——subprocess 阻塞，直接进事件循环会把整条 SSE 流卡死。

**版本**：官方示例写 `@latest`，本轮 `@latest` 解析到 **0.0.6**（registry 最后发布 2026-06-22）。适配层里 pin 成 `0.0.6`，属于**对官方示例的有意偏离**——抽取器依赖 Markdown 排版，版本一漂移正则就失效。代价：拿到的是旧版行为，升级必须连同正则一起改（见第 3 节）。

**[平台坑]** `channel.json` 由 CLI 从 **`process.cwd()`** 读取，不是从 skill 目录读。因此 Skill 目录里的 `channel.json` 不会被自动命中；本项目将其归档到 `references/skill-archives/meituan-travel-source/channel.json`，适配器显式传 `--channel meituan-developer`（该参数优先级最高）。

参数细节：`-o json` 是全局参数（子命令前后都接受）；`--city` **默认北京**，不显式传会把城市拼错——实测首次调用就是"北京 + 上海到南京"的混搭；`--origin-query` 官方标注必填（用于统计，不影响结果）。

### 2.3 响应契约

**运行期只有 CLI 的两种信封**，`tests/fixtures/` 里 4 个美团样本全部由官方 CLI 形态调用产生：

```jsonc
// 成功 → stdout
{"status":"success","data":"<Markdown>"}

// 失败 → stderr，且此时 stdout 为空
{"status":"error","error":{"code":"E_API_ERROR","message":"Bad Request"}}
```

CLI 另有 `{"code":-1,"msg":"未配置 Token..."}` 的配置期返回，但那种情况走的是 exit 3。

三条铁律：

1. **CLI 不透出服务端原因**。`error.message` 取的是 HTTP `statusText`，不是服务端的 `reason`。服务端本身有 6 键信封（`result` / `data` / `agentHint` / `errorInfo{errorLevel,retryable,errorCode,reason,fixFields,validOptions,retryAfterMs,failedItems}` / `code` / `msg`），设计上是留给 Agent 自愈用的，但**走 CLI 拿不到**——所以本项目的美团侧容错只能建立在**退出码**上，不要写出依赖 `retryable` 的分支。
2. **HTTP 200 不代表成功**（仅对直连成立）：业务失败照样返回 200，成败只在 `result` 字段。走 CLI 时这体现为"进程正常退出但 `status != success`"，所以**必须校验 `status`，不能只看退出码**。
3. 退出码：`0` 成功 / `1` 参数或网络错误 / `3` 鉴权失败（**已真机验证**：不给 token 即触发）/ `4` 渠道签名验证失败。

**超时**：官方文档写"约 1-2 分钟，网络超时 >120s"。实测 4 次：`15.2s`（高铁）、`17.5s`（酒店）、`23.8s`（高铁）、`29.7s`（机票）。**保持 120s 上限不下调**——样本只覆盖单跳票务，未测度假套餐与多约束复杂行程，且波动已达 2 倍。等待体验靠 2.7 的 `progress` 事件解决，不靠压缩超时。

### 2.4 `data` 是 Markdown，抽取器 [本项目实现]

`data` 外层裹着 `<answer>` 标签，内部按品类呈现三种不同排版。**以下三条正则均已对 fixture 验证通过。**

**机票**（6/6 命中，日期为完整 `YYYY-MM-DD`）与**高铁**（6/6 命中，日期是 `MM.DD`，时长带 `总` 前缀，车次可能是 `G44` 这种 2 位数字）：

```python
TRAFFIC = re.compile(
    r'(?P<no>[A-Z]{1,2}\d{1,4})\s*(?P<from>\S+?)→(?P<to>\S+?)\s*'
    r'(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2})\s*'
    r'(?P<dep>\d{2}:\d{2})→(?P<arr>\d{2}:\d{2})\s*总?(?P<dur>[^\s]+?)\s+(?P<seat>\S+?)\s*¥(?P<price>[\d.]+)'
)
# 原文形如：[G94 上海虹桥→南京南 11.26 08:55→09:54 总59m 二等座 ¥162](http://dpurl.cn/xxx)
```

**酒店**是散文，价格用「元」不用「¥」，且**酒店名在标题块、房型价在正文段落**，两者不同行：

```python
HOTEL = re.compile(r'\*\*(?P<room>[^*]+?)\*\*每晚约\*\*(?P<price>\d+(?:\.\d+)?)元\*\*')
# 原文形如：**特惠大床房**每晚约**78元**，**商旅双床房**每晚约**132元**
```

注意 6/6 这条只统计到房型价，**酒店名与房型要靠段落顺序配对**，是三个抽取器里最脆的一个。

**降级规则**：房型价抽取失败时，把该酒店标记为 `PRICE_UNVERIFIED` 并从预算求和中排除，同时在 `ItineraryValidator` 的冲突列表里显式输出——**绝不能静默按 0 元算进预算**。

**措辞敏感**：问"300 元以内的酒店"时返回里 **`¥` 与房型价全无**（只有评分和步行距离）；改成"住一晚多少钱，给出每晚房价"才拿到 6 条房型价。query 模板必须显式要求给出房价。

### 2.5 正文叙述不可信——必须用抽取值做核验

同一份返回里，**开头叙述的金额区间与结构化条目对不上**，而且偏差方向不定：

| 样本 | 正文声称 | 抽取到的实际 | 偏差 |
| --- | --- | --- | --- |
| 机票 | "票价低至 **380 元**起" | 最低 **¥350** | 正文**高估** 30 元 |
| 酒店 | "价格普遍在 **78元至180元**" | 最高 **181 元** | 正文**低估** 1 元 |

这就是 `ItineraryValidator` 必须存在的最硬理由：**预算核验只能建立在抽取后的 `tool_receipts` 数值上**，读那段散文汇总必然算错。写进简历时这条比"我们防止了模型编造车次"更有说服力，因为它是真实数据源的固有噪声，不是模型幻觉。

### 2.6 刷新样本

真实查询消耗账号配额，只在包版本变动或排版漂移后重跑：

```bash
uv run python -c "import sys;sys.path.insert(0,'src');from trippilot.tools.meituan_skill import run_query;print(run_query('上海到南京的高铁',city='南京')[:200])"
```

把输出存进 `tests/fixtures/meituan_<品类>_cli.json`（结构 `{"status":"success","data":...}`），跑 `uv run pytest -q` 看正则是否仍命中。**不要新增直连样本**——CLI 与直连信封不同构，混在一个目录里迟早写错解析分支。

### 2.7 官方要求的交互约束 [平台提供]

`SKILL.md` 不只是接口说明，它规定了 Agent 面对用户时的行为，两条容易漏：

1. **发起调用前必须先输出等待提示**（原文要求原样发送"正在连接美团酒旅数据接口为您规划，耗时约 1-2 分钟，请稍候…"）。对本项目意味着 SSE 协议里要有一个 `progress` 事件类型，而不是等 tool 返回才吐第一个字节。
2. **返回内容默认如实展示**，跳转链接要保留；价格字符串里的 `X`/`XX` 是后端脱敏占位符，**不要试图还原**。

第 1 条直接影响 Day 5 的网关设计，现在不记下来，写 `/api/plan/stream` 时一定会忘。

---

## 3. 版本与同步

平台不会通知你 Skill 变了，按来源分三种探测：

| 来源 | 探测方式 | 已实现 |
| --- | --- | --- |
| npm 包 | `registry.npmjs.org/<pkg>` 的 `dist-tags.latest` | `scripts/check_skill_updates.py` |
| Skill 平台 | 页面内嵌 `__SSG_DATA__` 的 `latestVersion` + `changelog`（`api.skillhub.cn/api/v1/*` 只接受 POST，GET 全 405） | 同上 |
| MCP Server | 无包可同步，但**协议会变**：盯官方 changelog + 启动时比对 `list_tools()` 工具名集合 | `tests/fixtures/amap_tools.json` 作基线 |

清单在 `src/skills/sources.json`（来源渠道、包名、pin 版本、端点、采用/存档判定）。

```bash
uv run python scripts/check_skill_updates.py   # 当前 4/4 OK
```

**版本真变了要做三件事**：改 `sources.json` 的 `package_latest` → 重跑 `pytest tests/test_meituan_contract.py tests/test_amap_mcp.py`（契约测试最容易先炸）→ diff 新旧 `SKILL.md` 看约束有没有变。

---

## 4. 测试基线

| fixture | 内容 | 被谁用 |
| --- | --- | --- |
| `meituan_flight_cli.json` | 机票成功响应，6 条 `¥` | 交通正则 |
| `meituan_train_cli.json` | 高铁成功响应，`MM.DD` 日期 + `总` 前缀时长 | 交通正则、年份歧义 |
| `meituan_hotel_cli.json` | 酒店成功响应，6 条房型价 | 酒店正则、品类互斥 |
| `meituan_error_cli.json` | CLI 失败信封（stderr，`{status,error}`） | 错误契约、双流解析 |
| `amap_tools.json` / `.args.json` | 15 个工具名与入参 | 高德契约 |

美团 4 个样本**全部由官方 CLI 形态调用产生**，与运行期解析的结构完全同构。

对应测试：`test_meituan_skill.py`（命令形态、stdout/stderr 双流、退出码分流）、
`test_meituan_contract.py`（信封字段）、`test_receipt_extraction.py`（三条正则）、
`test_amap_mcp.py`（工具契约、代理绕过）。

## 5. 未验证 / 风险清单

- **景区门票、度假套餐**没测过，第 4 种排版未知；正则目前只覆盖机票、高铁、酒店三类。
- 账号**配额与限流阈值**未知。走 CLI 拿不到 `retryAfterMs`，退避策略只能自己定。
- `MEITUAN_HT_TOKEN` 的权限范围未确认，只知道能通过网关鉴权（对照组：无 token 与乱填 token 都返回 401）。
- 高铁日期是 `MM.DD` **没有年份**，跨年行程（12 月末→1 月初）有歧义风险。
- 酒店名与房型价分行，配对依赖段落顺序，排版微调即失效。
- `dpurl.cn` 短链有效期未知，不适合长期存档。
- 实测波动已达 2 倍（15.2s → 29.7s），且只覆盖单跳票务；复杂行程是否真到 1-2 分钟未验证。
- **运行环境需要 Node 运行时**（`npx`），Docker 镜像必须同时装 Python 与 Node，且 npm registry 要可达。

## 6. 验收清单

- [ ] `uv run pytest -q` 全绿，且全程不发起任何网络请求
- [ ] 运行期不存在任何绕过 CLI 的 HTTP 调用（curl/httpx/requests 打 `mcp-open-cater.meituan.com`）
- [ ] 美团适配层同时读 stdout 与 stderr，并校验 `status` 字段，不只看退出码
- [ ] 超时上限保持 120s，未拿到复杂查询样本前不下调
- [ ] SSE 协议里有 `progress` 事件（官方要求调用前先发等待提示，见 2.7）
- [ ] `sources.json` 与适配层 `PINNED_VERSION` 一致，`check_skill_updates.py` 全 OK
- [ ] 高德调用链路上无 `HTTP_PROXY` 残留（`trust_env=False` 生效）
- [ ] 预算求和路径中不存在"价格缺失按 0 计"的分支，且不使用正文叙述里的汇总金额
