# astrbot_plugin_cs2_results

将 HLTV CS2 顶级赛事战报机器人移植为 AstrBot + NapCat(OneBot v11)插件。

## 功能

- 顶级赛事直播时逐图推送战报卡,包含比分、半场、十人 Rating 和 VRS 变化。
- 比赛开始时推送开赛卡,包含双方阵容、Major 冠军星标和 VRS 预测。
- 支持 `/cs2 赛事`、`/cs2 日程`、`/cs2 赛程 [赛事名]`。
- 支持群订阅、战队订阅和选手订阅,个人订阅命中后会在群里 `@` 对应用户。
- 支持补报、投递重试、禁言顺延、死信重放和持久化 outbox。
- 使用 AstrBot 内置 `Star.html_render()` / T2I 服务渲染卡片。
- HLTV 页面和图片支持 `curl_cffi` Chrome TLS 指纹优先通道,并可按配置走代理。

## 安装

1. 将整个 `astrbot_plugin_cs2_results` 目录放到 AstrBot 的 `data/plugins/` 下。
2. 在 AstrBot WebUI 中安装插件依赖,或手动执行:

```bash
pip install -r requirements.txt
playwright install chromium
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
| `/cs2 日程` | 查看当前或下一个比赛日的赛程与赛果 |
| `/cs2 赛程 [赛事名]` | 查看正在进行或即将开赛赛事的完整赛程 |

调试群或超级管理员私聊还支持以下命令:

| 指令 | 说明 |
| --- | --- |
| `/cs2 状态` | 查看抓取、缓存和投递状态 |
| `/cs2 测试 [比赛ID或URL]` | 立即渲染一张测试战报卡 |
| `/cs2 重试投递 [比赛ID]` | 重新激活死信投递 |
| `/cs2 刷新名录` | 强制刷新战队和选手名录 |
| `/cs2 刷新VRS` | 强制刷新 Valve 世界排名 |

## 抓取通道

- `cs2_use_curl_cffi` 默认开启。插件优先使用 `curl_cffi.AsyncSession(impersonate="chrome")`
  抓取 HLTV 页面、搜索 JSON 和赛事图片。
- `cs2_proxy_url` 为空时,依次沿用 `HTTPS_PROXY`、`https_proxy`、`HTTP_PROXY`、
  `http_proxy` 环境变量。
- `curl_cffi` 遇到 HTTP/Cloudflare 挑战、超时或响应类型异常时自动回退 Playwright。
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
