# MAP: 路径拓扑与架构映射

本文档记录本项目在本地开发环境、远端部署环境以及源码层级的目录与配置拓扑关系。

---

## 1. 物理环境与路径映射

| 作用域 | 实体 | 路径 / 标识 | 说明 |
| :--- | :--- | :--- | :--- |
| **本地工作区** | 代码根目录 | `.` (工作区根目录) | Git 仓库主目录 |
| **本地工作区** | Python 虚拟环境 | `.venv/` | Python 3.12+ 依赖环境 |
| **本地工作区** | 构建打包脚本 | `scripts/build_release.ps1` | 生成排除缓存与大型素材的纯净 ZIP |
| **远程生产主机** | 部署架构 | Linux VPS / Docker 容器化 | 运行 AstrBot + OneBot 协议端 |
| **远程生产主机** | 核心 Bot 容器 | `astrbot` | 内部工作目录 `/AstrBot` |
| **远程生产主机** | 协议实现容器 | `napcat` | OneBot v11 协议端 |
| **远程生产主机** | 插件生产目录 | `/opt/astrbot/data/plugins/cs2_results` | 宿主机挂载目录，代码生效位置 |
| **远程生产主机** | 数据持久化目录 | `/opt/astrbot/data/plugin_data/cs2_results` | 存放 `state.sqlite3`、Cookies、Logo 缓存 |
| **远程生产主机** | 插件配置文件 | `/opt/astrbot/data/config/cs2_results_config.json` | 生产环境真实生效参数 |
| **远程生产主机** | 安全备份隔离区 | `/root/backup_plugins/` | **唯一允许存放历史备份的目录** |

---

## 2. 核心源码职责映射

```
astrbot_plugin_cs2_results/
├── main.py              # 指令入口 (handle_cs2, _handle_player_detail) 与生命周期调度
├── fetcher.py           # 抓取中枢 (_FairPriorityGate, _navigate_html, Playwright 调度)
├── hltv.py              # HLTV HTML 结构解析器 (队伍阵容、选手资料、VRS/HLTV 榜单解析)
├── store.py             # SQLite3 数据总线 (team_index, player_team_cache, vrs_ranking)
├── render.py            # HTML 模板构建器与 AstrBot html_render 桥接
├── names.py             # 中文俗称/外号与权威 ID 映射词典及仲裁算法
├── delivery.py          # 消息重试、去重、持久化 outbox 与限速投递器
├── news.py              # HLTV RSS 订阅与大模型翻译总结分发
├── news_entities.py     # 资讯实体抽取与选手/战队关联
├── config.py            # 插件配置项 Schema 定义与默认值
├── _conf_schema.json    # AstrBot WebUI 配置面板元数据
└── metadata.yaml        # AstrBot 官方插件元数据定义 (版本号管理)
```

---

## 3. 同步边界与安全红线

### 🔴 边界 A: 严禁污染 AstrBot 插件扫描区
- **红线规定**: 严禁在 `/opt/astrbot/data/plugins/` 内部及其任何子层级创建 `.bak`、`backup`、临时文件或放置未解压 ZIP。
- **触发原因**: AstrBot 启动和重载时会全量遍历 `data/plugins/` 下的每一个文件夹尝试作为 Python 插件加载。非标准文件夹将直接导致核心引擎启动抛错或死锁。
- **标准操作**: 任何远程实机备份统一放至宿主机 `/root/backup_plugins/`。

### 🔴 边界 B: 严禁在 GitHub Release 暴露测试日志
- **红线规定**: 发布 Release Notes 时，严禁包含形如 `本地测试：104 passed (pytest)` 或 `VPS 实机环境验证测试日志` 等内部环境流水账。
- **标准操作**: Release Notes 仅面向玩家与 Bot 管理员，专注罗列业务变更点、功能特性与修复项。
