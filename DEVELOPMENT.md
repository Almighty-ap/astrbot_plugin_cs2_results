# CS2 Results 插件开发文档

本文档面向继续维护和扩展 `astrbot_plugin_cs2_results` 的开发者。当前开发基线为
`v1.5.8`（2026-10-10），目标运行时是 AstrBot `>=4.25.3,<5` 与
NapCat/OneBot v11。

## 1. 项目定位

本项目是 [canxiaocai/cs2-event-bot](https://github.com/canxiaocai/cs2-event-bot)
的 AstrBot 移植版。它保留原始项目的 HLTV 赛事追踪、逐图战报、赛程查询和订阅告警
逻辑，同时增加 AstrBot 命令、LLM Function Calling、AstrBot T2I 渲染和主动消息投递。

主要能力：

- 自动追踪 HLTV 顶级赛事直播，并在每张地图结束后推送战报卡。
- 推送开赛提醒，支持群级、战队级和选手级订阅。
- 查询未来赛事、当前比赛日、赛事赛程和指定战队近期比赛。
- 支持 `/cs2 查询 选手|战队` 详情卡，展示选手近期数据、战队阵容与排名。
- 轮询 HLTV RSS，翻译资讯，并向订阅会话推送资讯卡。
- 将赛事查询注册为 AstrBot LLM 工具。
- 使用持久化 outbox 处理战报和资讯投递的重试、禁群顺延、死信和重启恢复。
- 支持通过 noVNC/x11vnc 人工完成 Cloudflare Turnstile，并持久化浏览器登录状态。

## 2. 总体架构

```text
AstrBot 事件/命令/LLM 工具
            |
            v
    Cs2ResultsPlugin (main.py)
            |
            +--> 命令解析、权限、冷却、收件人计算
            |
            +--> Fetcher --> HLTV / RSS / 图片 CDN
            |        |
            |        +--> curl_cffi 优先
            |        +--> Playwright 回退
            |
            +--> hltv.py 解析领域模型
            |
            +--> render.py 生成 HTML，调用 AstrBot T2I
            |
            +--> store.py 持久化订阅、去重、缓存和 outbox
            |
            +--> DeliveryWorker 主动发送 PNG 到群
```

自动推送与命令查询是两条并行的链路：

```text
自动推送:
定时扫描 -> 发现直播/补报 -> 追踪比赛 -> 生成战报 -> outbox -> 群

用户查询:
/cs2 命令或 LLM 工具 -> 读取缓存/抓取 -> 渲染卡片 -> 当前会话

资讯推送:
RSS 轮询 -> 去重 -> LLM 翻译 -> 渲染 -> 资讯 outbox -> DeliveryWorker -> RSS 订阅会话
```

## 3. 目录职责

| 文件 | 职责 |
| --- | --- |
| `main.py` | AstrBot 入口、命令路由、后台任务、赛事追踪、收件人计算和 LLM 工具 |
| `config.py` | Pydantic 配置模型、默认值和跨字段校验 |
| `_conf_schema.json` | AstrBot WebUI 配置表 |
| `fetcher.py` | 抓取、缓存、限流、Cloudflare 退避、Playwright 状态复用和人工验证 |
| `hltv.py` | HLTV HTML/JSON 解析和领域数据模型 |
| `store.py` | SQLite 状态库、JSON 状态、图片/页面缓存、战报与资讯 outbox |
| `delivery.py` | 消费战报和资讯投递，处理重试、禁言顺延、退订清理和死信 |
| `render.py` | 战报、开赛、赛程、资讯、选手/战队详情和帮助卡的 HTML/PNG 渲染 |
| `news.py` | HLTV RSS 轮询、翻译、去重、持久化入队和提及匹配 |
| `news_entities.py` | 本地战队/选手实体匹配，不调用 LLM 做命名实体识别 |
| `names.py` | 战队/选手名字归一化、本地解析和别名处理 |
| `majors.py` | Major 冠军星标数据 |
| `security.py` | 比赛 ID/URL 输入白名单校验 |
| `tests/` | 单元测试和最小 AstrBot 测试桩 |
| `docs/images/` | README 使用的功能预览图 |

## 4. AstrBot 接入

### 4.1 插件入口

入口类是 `Cs2ResultsPlugin(Star)`。

`initialize()` 负责：

1. 保存 AstrBot `Context`。
2. 将 WebUI 配置转换为 `Config`。
3. 读取 AstrBot 管理员 QQ 列表。
4. 创建 `Fetcher` 和 `DeliveryWorker`。
5. 将 `self.html_render` 绑定到渲染模块。
6. 启动轮询、outbox、资讯、赛事预热和每日任务。

`terminate()` 负责取消全部后台任务、释放 outbox claim，并关闭 Playwright/Xvfb 与
人工验证辅助进程。

### 4.2 事件和命令

- `@filter.command("cs2")` 是唯一公开命令入口。
- `remember_origins()` 记录主动推送所需的 `unified_msg_origin`。
- `handle_onebot_notice()` 处理机器人离群、成员退群、群禁言和解除禁言。
- 命令统一进入 `handle_cs2()`，再按子命令分发。
- 管理命令只在调试群或超级管理员私聊中可见和可用。

命令层通过以下适配对象复用原 NoneBot 风格核心：

| 适配对象 | 作用 |
| --- | --- |
| `_LegacyEvent` | 将 AstrBot 事件暴露为原代码使用的 `group_id`、`user_id`、`sender` 等属性 |
| `_CommandContext` | 将 `send()` / `finish()` 转为 AstrBot `MessageChain` 和 `event.send()` |
| `_LegacyCommand` | 通过 `ContextVar` 让旧核心调用 `cs2.send()` / `cs2.finish()` |

### 4.3 主动发送

主动发送依赖 `unified_msg_origin`，格式为：

```text
<platform_id>:GroupMessage:<group_id>
<platform_id>:FriendMessage:<user_id>
```

群会话优先使用数据库中记录的 origin，缺失时回退到当前 `aiocqhttp` 平台实例。
`DeliveryWorker` 通过 `Context.send_message(umo, MessageChain)` 发送。

### 4.4 T2I 渲染

`render.py` 只负责构造 HTML。真正截图由 AstrBot 的 `Star.html_render()` 完成：

```python
await self.html_render(
    html,
    {},
    return_url=False,
    options={"type": "png", "full_page": True, "scale": "device"},
)
```

部署环境必须配置可用的 AstrBot T2I 服务。Playwright 是 HLTV 抓取回退通道，
不能替代 T2I。

## 5. 后台任务

插件启动后主要运行以下任务：

| 任务 | 作用 |
| --- | --- |
| `_poll_loop` | 串行调度 `/matches` 扫描、补报和直播比赛轮询 |
| `_outbox_loop` | 持续消费战报和资讯两类持久化投递队列 |
| `NewsService.poll_loop` | 按配置轮询 HLTV RSS |
| `_run_interval(_job_warm_event)` | 保鲜“正在进行”赛事的赛程页缓存 |
| `_run_daily(_job_featured)` | 每日刷新赛事白名单、logo 和本地名录 |
| `_run_daily(_job_cleanup)` | 每日清理页面、logo、去重和投递记录 |
| `_run_daily(_job_vrs)` | 每日刷新 Valve 世界排名快照 |

所有后台任务都通过 `_spawn_background()` 登记。命名相同的任务会被去重，
关闭插件时会统一取消并等待退出。

## 6. 赛事追踪流程

### 6.1 白名单

`refresh_whitelist()` 抓取 HLTV `/events`，解析 `#FEATURED` 和 `.big-event`，
写入本地白名单。`cs2_force_include_events` 和 `cs2_force_exclude_events`
可以覆盖自动结果。

### 6.2 扫描直播

`scan_live()` 抓取 `/matches`，同时考虑：

- 白名单赛事。
- 个人战队订阅。
- 被订阅选手的当前所属队伍。

符合条件的新比赛会加入 `_followed`。

### 6.3 轮询比赛

`follow_match()` 抓取比赛页并完成：

- 解析大场、小图、比分、选手 Rating、阵容和 VRS。
- 补齐队标和赛事 logo。
- 顺路刷新本地战队/选手名录。
- 计算本场收件群及每群要 `@` 的成员。
- 第一次发现比赛时生成开赛卡。
- 每发现一张已完成且尚未推送的地图，就生成一张战报卡。

轮询间隔根据当前地图回合数自适应，并受全局抓取节流影响。

### 6.4 补报

`scan_backstop()` 从 `/results` 查找最近结束但未完整推送的比赛，重新纳入追踪。
首次启动使用更宽的窗口，用于覆盖机器人离线过夜的情况。

## 7. 持久化

运行数据目录：

```text
<AstrBot data>/plugin_data/cs2_results/
```

### 7.1 SQLite

当前 schema 版本为 `6`，主要表：

| 表 | 用途 |
| --- | --- |
| `metadata` | schema 版本、刷新时间、会话 origin 等键值状态 |
| `subscriptions` | 群级战报订阅 |
| `subscription_targets` | 群内用户的战队/选手订阅 |
| `player_team_cache` | 选手当前所属队伍缓存 |
| `team_index` | 本地战队名录 |
| `vrs_ranking` | Valve 世界排名 |
| `delivery_batches` | 每个 `(match_id, map_key)` 的 PNG 载荷 |
| `deliveries` | 每个群的投递状态、尝试次数、lease 和 mentions |
| `news_deliveries` | 每条资讯在每个订阅会话中的投递状态和 mentions |

Outbox 的关键语义：

- 战报卡片先写入 `delivery_batches`，再进入 `deliveries`。
- 资讯卡片先渲染并写入 `delivery_batches`，再按完整 UMO 写入
  `news_deliveries`；GUID 只在成功入队后标记为已见。
- 只有成功发送才标记为 `sent`。
- 普通失败使用有上限的指数退避。
- 群禁言会顺延且不消耗重试次数。
- 永久失败会进入 `dead` 或自动退订不可达群。
- 重启后仍可继续投递已经生成的战报或资讯载荷。

### 7.2 JSON 和文件缓存

以下数据仍使用 JSON 或文件：

- 赛事白名单。
- 推送去重记录。
- 资讯订阅和 GUID 去重。
- 事件 logo URL 映射。
- HTML 页面缓存。
- logo 图片缓存。

新增持久化数据前，先判断是否必须进入 SQLite。需要事务、并发 lease 或逐项状态时
应使用 SQLite；简单低频键值状态可以继续使用 JSON。

## 8. 命令一览

公开命令：

| 命令 | 行为 |
| --- | --- |
| `/cs2` | 图片帮助卡 |
| `/cs2 订阅` / `/cs2 退订` | 群级战报订阅，需群主、管理员或 AstrBot 管理员 |
| `/cs2 订阅 战队 <名字>` | 个人战队订阅 |
| `/cs2 订阅 选手 <名字>` | 个人选手订阅 |
| `/cs2 退订 战队\|选手 <名字>` | 移除个人订阅 |
| `/cs2 我的订阅` | 查看当前群内的个人订阅 |
| `/cs2 赛事` | 未来三个月顶级赛事 |
| `/cs2 日程` | 当前或下一个比赛日 |
| `/cs2 战况 <战队>` | 按战队过滤日程结果 |
| `/cs2 查询 选手 <名字>` | 选手近期 Rating/K/D、角色分和 Major 荣誉 |
| `/cs2 查询 战队 <名字>` | 战队阵容、世界/VRS 排名和近期战绩 |
| `/cs2 赛程 [赛事名]` | 进行中赛事的完整赛程 |
| `/cs2 资讯` | 最新 HLTV RSS 资讯 |
| `/cs2 资讯订阅` / `资讯退订` | RSS 自动推送订阅 |
| `/cs2 资讯状态` | RSS 轮询和去重状态 |

管理命令仅在调试群或超级管理员私聊中可用：

| 命令 | 行为 |
| --- | --- |
| `/cs2 状态` | 运行状态、缓存、投递和失败来源 |
| `/cs2 测试 [比赛ID或URL]` | 立即生成一张测试战报 |
| `/cs2 重试投递 [比赛ID]` | 重放死信 |
| `/cs2 刷新名录` | 强制刷新战队/选手名录 |
| `/cs2 刷新VRS` | 强制刷新 Valve 排名 |
| `/cs2 验证` | 启动可见 Chromium 和 noVNC，人工完成 Cloudflare 验证 |
| `/cs2 资讯检查` | 立即检查并推送 RSS |

命令冷却默认按群或私聊用户计算。LLM 工具调用通过事件 extra 跳过普通命令冷却，
但受 `cs2_llm_tool_max_image_calls` 限制。

## 9. LLM 工具

注册的工具：

| 工具 | 内部命令 |
| --- | --- |
| `query_cs2_events` | `赛事` |
| `query_cs2_schedule` | `日程` |
| `query_cs2_bracket` | `赛程 [event_name]` |
| `query_cs2_news` | `资讯` |
| `query_cs2_match_status` | `战况 [team]` |

工作方式：

1. 工具调用 `_run_llm_tool_command()`。
2. 该函数复用 `handle_cs2()`，并把结果直接发送到当前事件会话。
3. 函数返回简短摘要给模型继续组织自然语言回复。
4. 通过 `_CommandContext(stop_event=False)` 避免工具调用终止整个 LLM 事件链。

`add_cs2_llm_tool_hint()` 在消息命中 CS2/HLTV 相关关键词时修改系统提示词，
引导模型优先调用工具。

## 10. 抓取与反爬

### 10.1 通道选择

- 默认优先使用 `curl_cffi.AsyncSession(impersonate="chrome")`。
- 遇到 Cloudflare 挑战、超时、错误内容类型或导入失败时回退 Playwright。
- Playwright 可按 `cs2_headful` 启动有头模式。
- Linux 无 DISPLAY 且启用有头模式时，会尝试启动私有 Xvfb。
- Playwright 会持久化 `storage_state`，并在达到导航或运行时阈值后整体回收浏览器栈。

### 10.2 URL 安全

抓取器只允许以下 HTTPS host：

- 页面：`hltv.org`、`www.hltv.org`
- 资源：`hltv.org`、`www.hltv.org`、`img-cdn.hltv.org`

用户提供的比赛 URL 还会经过 `security.hltv_match_url()` 二次校验。

### 10.3 优先级和节流

`_FairPriorityGate` 的顺序为：

```text
live > user > scan > warm
```

等待较久的低优先级任务会逐级老化，避免长期饥饿。`cs2_request_min_gap`
控制逻辑抓取之间的最小间隔，默认 90 秒；稳定部署可按原项目建议提高到 120 秒。
有直播时 `/matches` 至少每 5 分钟扫描一次，空闲时降到 10 分钟。赛事页预热默认
30 分钟且每轮只刷 1 个，避免装饰性请求抢占直播赛果额度。

Cloudflare 连续失败会触发 5、10、20、40、60 分钟指数退避。退避期间暂停
`scan`、`user`、`warm` 和 logo 请求，但保留 `live` 与手动验证通道。

RSS 使用 `ETag` 和 `Last-Modified` 条件请求。HTTP 304 不下载、不解析正文，
直接复用本地保存的上一份 XML。

### 10.4 页面缓存

命令和后台任务会使用两类缓存：

- fresh TTL：命中后直接返回。
- stale-while-revalidate：先返回旧副本，同时后台刷新。

直播追踪会持续重抓比赛页并回写缓存，命令链可以利用这些缓存补齐进行中 BO3/BO5
的当前大比分。

### 10.5 人工 Cloudflare 验证

`/cs2 验证` 仅在调试群或超级管理员私聊中开放。验证流程会：

1. 复用现有 `Xvfb :99` 或按需启动它。
2. 以有头模式启动 Chromium，并把窗口放在 `(0, 0)`。
3. 临时启动 `x11vnc` 和 noVNC，输出可从本机建立 SSH 隧道的连接命令。
4. 等待管理员在 HLTV 页面手动完成人机验证。
5. 检测到 `cf_clearance` 或成功进入目标页面后，保存 Playwright `storage_state`。

验证完成后浏览器上下文会复用保存的 cookies。后续自动抓取遇到失效时，再执行一次
`/cs2 验证` 即可。Docker 镜像需要安装 `x11vnc`、`novnc` 和 `websockify`。

## 11. 渲染约定

渲染模块采用“纯 HTML 构造 + AstrBot T2I”模式：

```text
build_*_html() -> HTML 字符串 -> Star.html_render() -> PNG bytes
```

主要入口：

- `render_map_card`
- `render_match_start_card`
- `render_events_card`
- `render_schedule_card`
- `render_event_schedule_card`
- `render_news_card`
- `render_help_card`

图片资源优先转为 data URI，避免 T2I 运行时再次访问外部网络。

## 12. 新增配置

新增配置时至少修改：

1. `config.py`：字段、默认值、范围和校验。
2. `_conf_schema.json`：WebUI 描述、类型、默认值和提示。
3. 使用该配置的业务代码。
4. 相关测试。
5. README 或本文档。

不要只增加配置而不接入实际行为，否则用户会看到“已配置但无效果”的选项。

## 13. 本地开发

### 13.1 环境

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
```

### 13.2 测试和静态检查

```bash
python -m pytest
ruff check .
```

测试通过 `tests/conftest.py` 注入最小 AstrBot 桩，不会启动真实 NapCat 或 Playwright。

主要测试范围：

- HLTV fixture 解析。
- SQLite 迁移、订阅和 outbox 状态机。
- `curl_cffi` 回退、代理和内容校验。
- 投递发送、mentions 和禁言顺延。
- 配置边界和 URL 安全。
- RSS 解析、实体匹配、条件请求和资讯 outbox 状态。
- Cloudflare 退避、人工验证状态和 Playwright `storage_state`。
- AstrBot 事件适配、T2I 调用和 LLM 工具限制。

### 13.3 在 AstrBot 中调试

1. 将整个插件目录放到 AstrBot 的 `data/plugins/`。
2. 安装插件依赖和 Playwright Chromium。
3. 在 WebUI 中启用插件或点击“重载插件”。
4. 配置至少一个订阅群或调试群。
5. 使用 `/cs2 状态` 和 `/cs2 测试 <比赛ID>` 验证抓取、T2I 和发送链路。
6. 在 Docker/VPS 中遇到 Cloudflare 时，使用 `/cs2 验证` 完成一次人工验证并观察
   后续抓取是否复用 `storage_state`。

真实端到端调试需要同时具备 AstrBot、NapCat、T2I 和可访问 HLTV 的网络环境。

## 14. 常见修改入口

| 需求 | 建议入口 |
| --- | --- |
| 增加微信公众号类命令 | `handle_cs2()` 和帮助卡渲染 |
| 增加新的 HLTV 页面解析 | `hltv.py` 和对应 fixture 测试 |
| 增加新的卡片 | `render.py`，保持 `build_*_html` / `render_*` 命名 |
| 增加持久化状态 | `store.py`，需要时提升 schema 并写迁移 |
| 修改主动投递策略 | `delivery.py` 和 `store.py` 的投递状态机 |
| 增加 LLM 工具 | `Cs2ResultsPlugin` 下的 `@filter.llm_tool` |
| 修改 RSS 行为 | `news.py`、`news_entities.py` 和资讯卡渲染 |
| 修改选手/战队详情 | `hltv.py`、`render.py` 和 `_handle_*_detail()` |
| 修改抓取限流 | `fetcher.py` 的 `_FairPriorityGate` 和 `Fetcher` |
| 修改 Cloudflare 验证 | `fetcher.py` 的 Playwright manager 和 `/cs2 验证` 入口 |

## 15. 当前已知缺口

这些缺口已经存在，不应被错误地认为功能已经完整接入：

- `cs2_rpm_limit` 尚未实现真正的每分钟请求计数限制。当前限流依赖请求最小间隔和
  优先级队列。
- `cs2_tpm_limit` 是预留配置，当前查询链不调用 LLM。
- `cs2_sub_start_window_min`、`cs2_sub_lineup_resolve_cap` 和
  `cs2_sub_player_team_refresh_hours` 目前没有业务代码读取。
- `/cs2 战况 <战队>` 只过滤日程数据，受顶级赛事白名单和结果回看窗口限制，不等价于
  查询任意战队的完整历史比赛。
- `main.py` 仍保留大量模块级全局状态，适合现有单体插件运行方式，但不利于隔离测试和
  多实例运行。
- 测试以单元和适配层为主，尚未覆盖真实 AstrBot + NapCat + T2I + HLTV 的端到端链路。

## 16. 发布检查

发布前至少完成：

1. 更新 `metadata.yaml` 版本号。
2. 更新 README 中的功能、命令和配置说明。
3. 运行 `python -m pytest`。
4. 运行 `ruff check .`。
5. 在真实 AstrBot 中执行一次重载、命令查询、主动推送和资讯推送。
6. 确认发布包没有包含运行时 `data/`、`__pycache__/`、`.pytest_cache/` 或本地密钥。
7. 检查 release 脚本和 README 预览图路径。

## 17. 参考

- AstrBot 插件开发指南：<https://docs.astrbot.app/dev/star/plugin-new.html>
- AstrBot 项目：<https://github.com/AstrBotDevs/AstrBot>
- 原始 CS2 Event Bot：<https://github.com/canxiaocai/cs2-event-bot>
- 本插件仓库：<https://github.com/Almighty-ap/astrbot_plugin_cs2_results>
