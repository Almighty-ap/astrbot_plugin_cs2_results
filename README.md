# astrbot_plugin_cs2_results

将 HLTV CS2 顶级赛事战报机器人移植为 AstrBot + NapCat(OneBot v11)插件。

> 本项目移植自 [canxiaocai/cs2-event-bot](https://github.com/canxiaocai/cs2-event-bot)，
> 原项目功能和卡片设计版权及许可归原作者所有。

## 预览

| 赛事日程 | 逐图战报 | HLTV 资讯 | 战队详情 |
| :---: | :---: | :---: | :---: |
| ![赛事日程预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview1.png) | ![逐图战报预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview2.png) | ![HLTV 资讯预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview3.png) | ![战队详情预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview6.png) |

<details>
<summary>帮助卡（命令一览）</summary>

![帮助卡](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview4.png?v=26f863a)

</details>

## 功能

- 顶级赛事直播时逐图推送战报卡,包含比分、半场、十人 Rating 和 VRS 变化。
- 比赛开始时推送开赛卡,包含双方阵容、Major 冠军星标和 VRS 预测。
- 支持 `/cs2 赛事`、`/cs2 日程`、`/cs2 赛程 [赛事名]`。
- 支持 `/cs2 查询 选手 <名字>` 查询近期 Rating / K/D、角色评分、Major 荣誉和近期比赛。
- 支持 `/cs2 查询 战队 <名字>` 查询现役阵容、HLTV / VRS 世界排名和近期战绩。
- `/cs2 日程` 在展示当前或下个比赛日时，会在顶部附带上一比赛日的已结束赛果。
- 支持群订阅、战队订阅和选手订阅,个人订阅命中后会在群里 `@` 对应用户。
- 战队与选手支持中文俗称与外号映射（如“小蜜蜂/小峰”→ Vitality、“绿龙”→ Spirit、“天禄”→ TYLOO、“载物”→ ZywOo、“森破”→ s1mple、“小孩/太子”→ m0NESY 等），支持使用俗称进行订阅、退订、战况筛选与 RSS 提及提醒。
- 支持 HLTV RSS 新闻订阅、自动去重、LLM 中文翻译总结和资讯卡片推送。
- 新闻提及已订阅战队或选手时，会在订阅群自动 `@` 对应用户。
- 支持 AstrBot LLM Function Calling，用户自然语言询问 CS2 比赛、赛程、比分和资讯时由模型调用插件查询。
- 支持补报、投递重试、禁言顺延、死信重放和持久化 outbox。
- 卡片默认由插件内置的浏览器栈（Camoufox）本地渲染，复用抓取层同一个内核，用完即由空闲看门狗释放；本地渲染异常时自动回退 AstrBot 的 `Star.html_render()` / T2I 服务。
- HLTV 页面和图片支持 `curl_cffi` TLS 指纹优先通道，并根据所选浏览器后端动态对齐 TLS 指纹与 User-Agent。
- 集成 **Camoufox**（定制 C++ 反特征检测 Firefox 内核），消除 CDP 特征与 Canvas/WebGL 自动化指纹泄漏。
- 内置 **TurnstileAutoSolver**，采用三阶贝塞尔曲线拟真位移与物理点击全自动穿透 Cloudflare Turnstile 挑战：优先等待挑战自行放行，8 秒仍未放行才补一次拟真点击，避免干扰可以自动放行的非交互挑战。
- 支持纯异步 CDP 直连内核 **nodriver** 作为备选通道，提供多层降级容灾。
- 针对 2C2G 云主机进行内存调优，支持 1-shot（单次即时回收）与空闲看门狗双重回收：抓取或渲染结束后 `cs2_browser_idle_timeout` 秒内没有新任务就销毁浏览器栈，实现平时 0 内存长驻。

## 安装

1. 将整个 `astrbot_plugin_cs2_results` 目录放到 AstrBot 的 `data/plugins/` 下。
2. 在 AstrBot WebUI 中安装插件依赖,或手动执行:

```bash
pip install -r requirements.txt

# 1. 核心推荐内核：Camoufox 反检测 Firefox（抗 Cloudflare 风控能力最强）
camoufox fetch

# 2. 备用标准内核：Playwright Chromium
playwright install chromium

# Linux 且开启有头调试模式时可选:
apt-get install -y xvfb
```

> **提示（资源受限机器优化）**：在 2C2G 等轻量云主机上部署 Camoufox 时，下载官方压缩包可剔除内置的 Windows/macOS 字体目录（约 2GB），解压后仅保留 Linux 必要依赖与字体（约 270MB）。同时建议保持默认的 `cs2_browser_recycle_uses: 1`，并配合 `cs2_browser_idle_timeout: 120`：抓取或渲染完成后最多两分钟无新任务即销毁进程，渲染峰值约 600MB、空闲回落至 200MB 以内。

3. 在 WebUI 中重载或启用插件。
4. 确保 NapCat 已通过 OneBot v11 连接到 AstrBot。
5. T2I 服务为可选项：卡片默认本地渲染，只有本地渲染失败时才会回退到 AstrBot 的 T2I。若 AstrBot 配置了远端 T2I，请留意其请求体上限（卡片 HTML 约 180KB，多数公共 T2I 服务上限在 20KB 左右，必然失败），因此本地渲染是推荐路径。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/cs2` | 查看图片帮助卡 |
| `/cs2 订阅` | 当前群加入战报推送,仅群主、群管理员或 AstrBot 管理员可用 |
| `/cs2 退订` | 当前群退出战报推送 |
| `/cs2 订阅 战队 <名字>` | 订阅战队,开赛和逐图战报会 `@` 订阅者 |
| `/cs2 订阅 选手 <名字>` | 订阅选手 |
| `/cs2 退订 战队\|选手 <名字>` | 取消个人订阅 |
| `/cs2 我的订阅` | 查看当前群的个人订阅 |
| `/cs2 赛事` | 查看未来三个月的顶级赛事 |
| `/cs2 日程` | 查看当前或下一个比赛日的赛程与赛果，顶部附带上一比赛日赛果 |
| `/cs2 战况 <战队>` | 查询指定战队近期比赛、当前比分与赛果 |
| `/cs2 排名 [vrs/hltv]` | 查看战队世界排名 TOP 20 榜单（默认 Valve VRS 榜 / 可选 HLTV 积分榜） |
| `/cs2 查询 选手 <名字>` | 查询选手近期 Rating / K/D、角色评分、Major 荣誉和近期比赛 |
| `/cs2 查询 战队 <名字>` | 查询战队现役阵容、世界 / VRS 排名和近期战绩 |
| `/cs2 赛程 [赛事名]` | 查看正在进行或即将开赛赛事的完整赛程 |
| `/cs2 资讯` | 查看最新一条 HLTV RSS 资讯卡片 |
| `/cs2 资讯订阅` | 当前会话订阅 HLTV RSS 自动推送 |
| `/cs2 资讯退订` | 取消当前会话的 RSS 订阅 |
| `/cs2 资讯状态` | 查看资讯轮询、订阅和去重状态 |

调试群或超级管理员私聊还支持以下命令:

| 指令 | 说明 |
| --- | --- |
| `/cs2 状态` | 查看抓取、缓存和投递状态 |
| `/cs2 验证` | 主动触发一次 Cloudflare 无感自动化过盾与凭据健康检测 |
| `/cs2 测试 [比赛ID或URL]` | 立即渲染一张测试战报卡 |
| `/cs2 重试投递 [比赛ID]` | 重新激活死信投递 |
| `/cs2 刷新名录` | 强制刷新战队和选手名录 |
| `/cs2 刷新VRS` | 强制刷新 Valve 世界排名 |
| `/cs2 资讯检查` | 立即检查并推送 RSS 新资讯 |

## RSS 资讯

- 默认订阅 HLTV 官方 RSS:`https://www.hltv.org/rss/news`。
- 首次运行只记录现有新闻 GUID,不补推历史消息,避免刷屏。
- 后续轮询只推送新增新闻,并按 GUID 持久化去重。
- 自动推送先持久化到 outbox,发送失败会按现有退避策略重试,避免单次超时丢新闻。
- 可调用 AstrBot 已配置的 LLM,将英文标题和摘要翻译总结成中文。
- 新闻卡片使用与战报、赛程相同的暖米色设计,支持 RSS 封面图。
- RSS 和封面图复用插件当前的 `curl_cffi` 指纹回退链与 `cs2_proxy_url` 代理通道。
- 新闻标题和原始描述只在本地的已订阅战队/选手中做完整词语匹配;支持 `NAVI`、
  `NIP`、`VP` 等战队别名及 `s1mple/simple` 这类昵称变体，不额外调用 LLM 做实体识别。

## LLM 工具

插件注册以下 AstrBot Function Calling 工具：

```text
query_cs2_events        查询未来三个月顶级赛事
query_cs2_schedule      查询当前或下一个比赛日
query_cs2_bracket       查询正在进行的赛事赛程
query_cs2_news          查询最新 HLTV RSS 资讯
query_cs2_match_status  查询指定战队近期比赛和比分
query_cs2_ranking       查询 CS2 战队世界排名 TOP 20 榜单
```

当用户消息包含 `CS2`、`HLTV`、比赛、赛程、战况、比分、战队、选手或资讯等关键词时，
插件会向当前 LLM 请求附加简短提示，引导模型优先调用对应工具。工具执行后直接发送
插件卡片，并返回简短摘要给模型继续回复。

相关配置：

```text
cs2_llm_tools_enabled          启用 LLM 工具
cs2_llm_tool_intent_hint       启用关键词工具提示
cs2_llm_tool_max_image_calls   单次对话最多发送的图片数
```

## 抓取通道与抗风控架构

面对 Cloudflare 针对数据中心/机房 IP 的严格风控（5秒盾 / Turnstile 人机验证），插件设计了兼具高性能与强抗检测的三层抓取架构：

```text
                      ┌─────────────────────────┐
                      │    业务抓取请求 (HTML)    │
                      └────────────┬────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │ L1: 优先级门禁与并发流控调度器   │
                    │   (按直播/用户/扫描分级退避)   │
                    └──────────────┬───────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼ (优先快速通道，耗时 < 100ms)                  │
 ┌──────────────────────────────────────┐                 │ (遇 CF 挑战 / 超时 / 凭证失效)
 │ L2: curl_cffi TLS 伪装抓取           │                 │
 │  · 动态对齐浏览器 TLS 指纹与 UA        │                 ▼
 │  · 复用 storage_state 中的有效凭据    │  ┌────────────────────────────────────────┐
 └──────────────────┬───────────────────┘  │ L3: 反检测浏览器内核 + 自动解盾管线         │
                    │ 成功                  │  · 核心内核: Camoufox (C++ 级特征反检测) │
                    ▼                      │  · 备用内核: nodriver (纯异步 CDP 直连)   │
            ┌───────────────┐              │  · 核心能力: Turnstile 自动穿透与拟真点击 │
            │ 返回目标 HTML  │              │  · 成功获取 cf_clearance 并原子写回磁盘  │
            └───────────────┘              └────────────────────────────────────────┘
```

### 核心特性说明

1. **Camoufox 反检测 Firefox 内核（默认推荐）**：
   - 传统 Chromium/Playwright 内核由于存在 CDP 通信特征（`Runtime.enable`）以及物理 GPU 适配器缺失等问题，在 Linux 无头 Docker 环境下极易被 Cloudflare 判定为自动化爬虫并拒签 Turnstile。
   - Camoufox 在 C++ 源码层对 Canvas、WebGL、WebRTC、AudioContext 与系统字体等进行了底层指纹重写，从根本上消除了自动化特征泄露。
2. **TurnstileAutoSolver 自动化解盾**：
   - 彻底废除了过时且需要人工干预的 `x11vnc` / `noVNC` 流程。
   - 内置三阶贝塞尔曲线（Cubic Bézier）人类行为轨迹模拟算法，通过隐藏字段 `input[name="cf-turnstile-response"]` 的父容器定位组件热区，派发带 `isTrusted: true` 属性的真实鼠标位移与点击，全自动穿透 Turnstile 挑战。
   - 交互式挑战先给 8 秒宽限期：托管挑战多数能自行放行，只有持续不放行才补一次拟真点击，避免多余交互反而延长校验。
   - 成功解盾后，自动提取 `cf_clearance` 并持久化落盘至 `playwright_storage_state.json`。
3. **动态 TLS 指纹自适应（curl_cffi）**：
   - Cloudflare 签发的 `cf_clearance` 凭证与客户端 TLS JA4 指纹和 User-Agent 强绑定。
   - 插件根据配置的 `cs2_browser_backend` 自动对齐 `curl_cffi` 的伪装目标（Camoufox 时匹配 Firefox 指纹及 UA，标准内核时匹配 Chrome 指纹），确保快速通道能稳定复用浏览器捕获的放行凭证。
4. **轻量内存与 1-shot 生命周期管理（2C2G 专属调优）**：
   - 在 2C2G 等轻量 VPS 上，浏览器若长驻后台会持续占用数百兆物理内存。
   - 默认开启单次即时回收（`cs2_browser_recycle_uses: 1`），当回退到浏览器完成一次抓取并落盘凭据后，立刻关闭浏览器并释放驱动堆内存。
   - 另有空闲看门狗兜底：最后一次浏览器操作（抓取或卡片渲染）后超过 `cs2_browser_idle_timeout` 秒无新任务，直接销毁整个浏览器栈，避免长期占用。
   - 完成过盾拿到 `cf_clearance` 后不会再主动删除已落盘的 `storage_state`，避免把仍有效的凭证清掉、导致下一次重新挑战。
5. **门禁与流控退避**：
   - 默认两次抓取间隔至少 `90` 秒；直播赛果与用户主动查询保持最高优先级，不会被周期扫描的冷却拖住。
   - Cloudflare 连续遇到拦截时按 `5/10/20/40/60` 分钟指数退避，退避期间暂停轮询，优先保障核心推送。
   - 退避期间对周期任务的抓取请求直接返回上一次成功的缓存页面，且不计入失败统计，不会再把“主动冷静”误报成“连续抓取失败”告警。
6. **代理配置**：
   - `cs2_proxy_url` 留空时依次读取系统 `HTTPS_PROXY`、`HTTP_PROXY` 环境变量。
   - 支持 `http://`、`socks5://` 与 `socks5h://`；Playwright 不接受 `socks5h://`，插件会在传给浏览器前自动改写为 `socks5://`（域名仍由代理端解析），而 `curl_cffi` 通道保留 `socks5h://` 原样使用。

### 相关配置项

| 配置字段 | 类型 | 默认值 | 说明 |
| --- | :---: | :---: | --- |
| `cs2_browser_backend` | string | `camoufox` | 浏览器回退后端：`camoufox`（推荐）、`nodriver`（实验性 CDP 直连）、`playwright`（标准 Chromium） |
| `cs2_turnstile_auto_solve` | bool | `true` | 是否启用 Turnstile 挑战自动化识别与贝塞尔轨迹模拟点击 |
| `cs2_turnstile_timeout` | int | `90` | 自动化解盾最大超时时间（秒，建议 60~120；挑战页通常需要数十秒自行校验，过短会误判失败） |
| `cs2_camoufox_os` | string | `windows` | Camoufox 伪装的操作系统特征：`windows` 或 `linux` |
| `cs2_browser_recycle_uses` | int | `1` | 浏览器最大复用次数（在 Camoufox 或 2C2G 机器上强制建议设为 `1` 即时回收） |
| `cs2_browser_recycle_hours` | float | `1.0` | 浏览器最大存活时间（小时），超时自动重建 |
| `cs2_browser_idle_timeout` | int | `300` | 浏览器空闲自动释放超时（秒，`0` 关闭看门狗；建议 120~300） |
| `cs2_use_curl_cffi` | bool | `true` | 是否开启 curl_cffi 快速抓取与指纹伪装通道 |
| `cs2_proxy_url` | string | `""` | 专用 HTTP/SOCKS5 代理地址，如 `http://127.0.0.1:7890`、`socks5h://127.0.0.1:7892` |

## 卡片渲染

卡片渲染分两步：`render.py` 只负责构造 HTML，截图由插件自己完成。

```text
build_*_html() -> HTML 字符串
      ├─ 本地渲染（默认）: Fetcher.render_html() -> Camoufox 新开 context -> full_page PNG
      └─ 失败回退: Star.html_render(..., return_url=False)，包在 60 秒超时内
```

- 本地渲染复用抓取层同一个 Camoufox 实例与内存调优参数，独立 context、`1080×900 @2x` 视口、45 秒超时，不额外拉起第二套内核。
- 渲染结束后把浏览器空闲计时往前拨，看门狗在约 60 秒后关掉整个浏览器栈；渲染期间容器峰值约 600MB，空闲后回落到 200MB 以内。
- 渲染结果必须是合法 PNG（校验文件头），错误页不会再被当成图片缓存或发给 QQ。
- 相同 HTML 命中进程内 PNG 缓存，重复查询 0 秒返回。
- 远端 T2I 只作为兜底：公共 T2I 服务普遍限制 20KB 级请求体，而卡片 HTML 约 180KB（内嵌字体），几乎必然 413/502，因此不要依赖远端渲染。

## 数据目录

运行时数据存储在 AstrBot 的:

```text
data/plugin_data/cs2_results/
```

包含订阅数据库、页面缓存、logo 缓存和投递载荷。升级或重装插件不会覆盖该目录。

## 开发与规范

- **架构设计与开发文档**: 详见 [DEVELOPMENT.md](DEVELOPMENT.md)

本地开发环境执行代码规范与静态检查：

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
```

## 说明

- 本项目为非官方项目,与 HLTV.org 无关联。
- 数据仅用于个人学习和非商业群内使用,请保留默认抓取间隔。
- NapCat 为非官方 QQ 协议实现,使用前请自行评估账号风险。
- 原项目使用 MIT License,字体 Hanken Grotesk 使用 SIL OFL 1.1。

## 致谢

衷心感谢 [canxiaocai](https://github.com/canxiaocai) 开发并开源
[cs2-event-bot](https://github.com/canxiaocai/cs2-event-bot)。原项目完成了 HLTV
赛事跟踪、战报渲染、订阅投递和赛事查询等核心设计，本 AstrBot 移植版是在其基础上
适配 AstrBot 与 NapCat 而成的。
