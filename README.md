# astrbot_plugin_cs2_results

将 HLTV CS2 顶级赛事战报机器人移植为 AstrBot + NapCat(OneBot v11)插件。

> 本项目移植自 [canxiaocai/cs2-event-bot](https://github.com/canxiaocai/cs2-event-bot)，
> 原项目功能和卡片设计版权及许可归原作者所有。

## 预览

| 赛事日程 | 逐图战报 | HLTV 资讯 |
| :---: | :---: | :---: |
| ![赛事日程预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview1.png) | ![逐图战报预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview2.png) | ![HLTV 资讯预览](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview3.png) |

<details>
<summary>帮助卡（命令一览）</summary>

![帮助卡](https://raw.githubusercontent.com/Almighty-ap/astrbot_plugin_cs2_results/main/docs/images/preview4.png)

</details>

## 功能

- 顶级赛事直播时逐图推送战报卡,包含比分、半场、十人 Rating 和 VRS 变化。
- 比赛开始时推送开赛卡,包含双方阵容、Major 冠军星标和 VRS 预测。
- 支持 `/cs2 赛事`、`/cs2 日程`、`/cs2 赛程 [赛事名]`。
- 支持 `/cs2 选手 <名字>` 查询近期 Rating / K/D、角色评分、Major 荣誉和近期比赛。
- 支持 `/cs2 战队 <名字>` 查询现役阵容、HLTV / VRS 世界排名和近期战绩。
- `/cs2 日程` 在展示当前或下个比赛日时，会在顶部附带上一比赛日的已结束赛果。
- 支持群订阅、战队订阅和选手订阅,个人订阅命中后会在群里 `@` 对应用户。
- 支持 HLTV RSS 新闻订阅、自动去重、LLM 中文翻译总结和资讯卡片推送。
- 新闻提及已订阅战队或选手时，会在订阅群自动 `@` 对应用户。
- 支持 AstrBot LLM Function Calling，用户自然语言询问 CS2 比赛、赛程、比分和资讯时由模型调用插件查询。
- 支持补报、投递重试、禁言顺延、死信重放和持久化 outbox。
- 使用 AstrBot 内置 `Star.html_render()` / T2I 服务渲染卡片。
- HLTV 页面和图片支持 `curl_cffi` Chrome TLS 指纹优先通道,并可按配置走代理。

## 安装

1. 将整个 `astrbot_plugin_cs2_results` 目录放到 AstrBot 的 `data/plugins/` 下。
2. 在 AstrBot WebUI 中安装插件依赖,或手动执行:

```bash
pip install -r requirements.txt
playwright install chromium
# Linux 且开启有头模式时:
apt-get install -y xvfb
```

`playwright` 用于 HLTV 反爬回退通道,即使用 AstrBot 内置 T2I 渲染卡片也需要安装。

3. 在 WebUI 中重载或启用插件。
4. 确保 NapCat 已通过 OneBot v11 连接到 AstrBot。
5. 确保 AstrBot 已配置可用的 T2I 渲染服务;离线部署可使用自部署 T2I。

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
| `/cs2 选手 <名字>` | 查询选手近期 Rating / K/D、角色评分、Major 荣誉和近期比赛 |
| `/cs2 战队 <名字>` | 查询战队现役阵容、世界 / VRS 排名和近期战绩 |
| `/cs2 赛程 [赛事名]` | 查看正在进行或即将开赛赛事的完整赛程 |
| `/cs2 资讯` | 查看最新一条 HLTV RSS 资讯卡片 |
| `/cs2 资讯订阅` | 当前会话订阅 HLTV RSS 自动推送 |
| `/cs2 资讯退订` | 取消当前会话的 RSS 订阅 |
| `/cs2 资讯状态` | 查看资讯轮询、订阅和去重状态 |

调试群或超级管理员私聊还支持以下命令:

| 指令 | 说明 |
| --- | --- |
| `/cs2 状态` | 查看抓取、缓存和投递状态 |
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
- RSS 和封面图复用插件当前的 `curl_cffi` 指纹回退链与 mihomo 代理。
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

## 抓取通道

- `cs2_use_curl_cffi` 默认开启。插件优先使用 `curl_cffi.AsyncSession(impersonate="chrome")`
  抓取 HLTV 页面、搜索 JSON 和赛事图片。
- `cs2_proxy_url` 为空时,依次沿用 `HTTPS_PROXY`、`https_proxy`、`HTTP_PROXY`、
  `http_proxy` 环境变量。
- `curl_cffi` 遇到 HTTP/Cloudflare 挑战、超时或响应类型异常时自动回退 Playwright。
- `cs2_headful` 默认为关闭。若 HLTV 的 `/events` 对无头浏览器返回挑战,可开启该选项;
  Linux 无桌面环境时插件会自动启动私有 `Xvfb`。
- Docker 内的 `127.0.0.1` 指向容器自身。代理运行在宿主机时,请填写宿主机地址或使用
  宿主机的容器网络地址。

## 数据目录

运行时数据存储在 AstrBot 的:

```text
data/plugin_data/cs2_results/
```

包含订阅数据库、页面缓存、logo 缓存和投递载荷。升级或重装插件不会覆盖该目录。

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
ruff check .
```

单元测试会为 AstrBot 运行时提供最小测试桩,不会启动真实 NapCat。

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
