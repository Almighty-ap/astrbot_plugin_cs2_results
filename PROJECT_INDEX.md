# PROJECT_INDEX: 项目索引与任务路由

本文档为项目简要定位与“任务场景 → 目标文件/章节”条件路由表。需要定位工作对象时查阅本索引，不复制专题正文。

---

## 1. 项目定位

`astrbot_plugin_cs2_results` 是面向 AstrBot (v4.x) 生态的 CS2 赛事战报、战队/选手数据与实时推送插件。核心服务群聊与个人订阅场景，通过对接 HLTV 与 Valve VRS 官方数据源，提供赛程查询、赛果卡片、TOP 20 世界排名榜单、资讯推送及开赛/@提醒等功能。

- **适用运行时**: AstrBot `>=4.25.3,<5` + OneBot v11 (NapCat)
- **当前发布版本**: `v1.7.2`
- **版本核心特性**:
  - 本地 SQLite 优先查询：选手/战队详情查询优先检索本地名录，阻断在线网络搜索与排队风控，首句提示降至毫秒级（< 1s）。
  - 中文别名库增补：新增 12 组选手高频外号（`孩贼王`、`千早爱烟`、`练习哥`、`点圣`、`紫薇`、`911`、`pdf`、`虾哥` 等）及全套单测。
  - 选手 Stats 可选页超时预算防护，遇盾秒级降级不卡死出图。

---

## 2. 任务场景与文档路由

根据当前工作任务类型，按需路由到对应文档或章节：

| 任务场景 / 问题类型 | 目标文件 / 权威入口 | 核心查阅内容 |
| :--- | :--- | :--- |
| **接手项目 / 确认规则** | [AGENTS.md](AGENTS.md) | 代理行为准则、读取规则、写入规则、验证要求与铁律 |
| **了解功能与使用方式** | [README.md](README.md) | 插件安装部署、指令列表、数据目录及预览示例 |
| **架构设计与底层机制** | [DEVELOPMENT.md](DEVELOPMENT.md) | 数据流、HLTV 抓取流程、持久化 outbox 与 T2I 渲染机制 |
| **接续当前工作 / 排期进度** | [docs/context/NOW.md](docs/context/NOW.md) | 当前在办焦点（查询耗时优化、别名发版）、进展、阻塞与下一步 |
| **环境映射与代码职责定位** | [docs/context/MAP.md](docs/context/MAP.md) | 物理环境拓扑、代码与模块职责映射、同步边界与红线 |
| **执行测试、运维或发版** | [docs/context/RUNBOOK.md](docs/context/RUNBOOK.md) | 本地测试 SOP、VPS 热更新 SOP、Release 构建发版流程 |
| **架构选型与决策背景** | [docs/context/DECISIONS.md](docs/context/DECISIONS.md) | 已确认 ADR：本地 SQLite 零网络出图、抓取底座、优先级门禁、别名算法 |
| **排查风控、死锁与踩坑红线**| [docs/context/RISKS.md](docs/context/RISKS.md) | AstrBot 插件反射扫描死锁、Cloudflare 403 连锁退避、浏览器锁积压 |
| **追溯历史事故与技术演进** | [docs/context/history/README.md](docs/context/history/README.md) | 2026-10 查询耗时突增现场排查、排名榜单与别名字典落地纪实 |

---

## 3. 核心代码模块路由

| 功能领域 | 核心文件 | 职责定位 |
| :--- | :--- | :--- |
| **命令入口与生命周期** | `main.py` | 指令分发 (`/cs2`)、后台轮询调度、消息事件总线桥接 |
| **数据采集与反爬控制** | `fetcher.py`, `hltv.py` | Playwright 浏览器调度、`_FairPriorityGate` 节流门、页面结构解析 |
| **数据持久化与缓存** | `store.py` | SQLite3 驱动（赛事、战队名录、榜单数据、订阅关系与投递队列） |
| **卡片构建与出图** | `render.py` | 米色优雅系卡片 HTML/CSS 模板构建与 AstrBot T2I 桥接 |
| **中文俗称与智能映射** | `names.py` | 社区战队/选手别名归一化、权威 ID 映射与排名权重仲裁 |
| **资讯追踪与大模型翻译** | `news.py`, `news_entities.py` | HLTV RSS 轮询、大模型新闻中文翻译总结与实体关联 |
| **可靠投递与队列** | `delivery.py` | 指数退避重发、死信队列与主动投递管道 |