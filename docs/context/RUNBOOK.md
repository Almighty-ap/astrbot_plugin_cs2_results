# RUNBOOK: 操作与维护手册 (SOP)

> [!IMPORTANT]
> **安全与授权声明**:
> 1. 本手册记录已验证的标准操作流程（SOP）。
> 2. **命令已配置不等于执行成功，文档中的操作步骤不构成执行授权**。
> 3. 涉及远程生产环境变更、代码发布或破坏性操作前，必须获取明确授权并在前置条件满足后执行。

---

## 1. 本地开发与质量验证 SOP

### 前置条件
- 已激活项目本地虚拟环境（如 `.venv/`）。
- 依赖项已安装（`pip install -r requirements.txt -r requirements-dev.txt`）。

### ⚡ Agent 命令执行防超时规范 (避开 10 秒异步转接)
> [!WARNING]
> 反重力任务调度器内置 **10 秒前台等待上限**（`WaitMsBeforeAsync: 10000`）。若命令耗时超过 10 秒，调度器将强制将其转入后台异步任务并派生子任务（如 `task-xx`）进行监听，易导致任务流失步与控制台信号混乱。
> - **常规验证必须定向执行**: 验证修改范围时，坚决优先执行定向模块测试（如 `pytest tests/test_names.py`，耗时 ~0.2 秒）或 `-k` 关键词单跑。
> - **全量测试注意**: 全量 117+ 用例耗时极易逼近或突破 10 秒。若必须执行全量回归，应明确知晓其转异步特性并合理等待后台通知，严禁原地死循环轮询。

### 常用检查命令
```powershell
# 1. [推荐] 针对指定模块 (如中文外号别名库) 执行专项单测 (< 1s 毫秒级返回)
pytest tests/test_names.py -v

# 2. [推荐] 按用例名或函数关键字定向过滤单跑
pytest -k "test_player_alias" -v

# 3. 运行代码风格与类型静态检查 (< 1s 快速返回)
ruff check .

# 4. [发版前审查] 全量单元测试 (可能触发 10s 阈值转后台异步，需配合任务管理接收完成通知)
pytest -v
```

---

## 2. 远程 VPS 热更新 (Hotfix) SOP

当需要将单个修复文件（如 `names.py`）同步至远程生产容器时，遵循以下三步防错流程：

### 步骤 1: 远端安全备份 (前置红线)
*在覆盖生产代码前，必须先在安全隔离区进行快照备份：*
```bash
# [生产规范] 严禁备份在 /opt/astrbot/data/plugins/ 内部！
mkdir -p /root/backup_plugins && cp /opt/astrbot/data/plugins/cs2_results/names.py /root/backup_plugins/names.py.bak_$(date +%s)
```

### 步骤 2: 文件安全同步
- 将本地更新后的目标文件通过 SFTP / SCP 上传至 `/opt/astrbot/data/plugins/cs2_results/` 覆盖对应文件。
- 校验文件 MD5 哈希确保传输完整无损：
```bash
md5sum /opt/astrbot/data/plugins/cs2_results/names.py
```

### 步骤 3: 容器重启与服务校验
```bash
# 重启 AstrBot 容器使热更代码生效
docker restart astrbot

# 观察容器实时日志，核查插件装载状态与是否存在报错
docker logs --tail 80 astrbot | grep -a -E 'cs2|star_manager'
```

---

## 3. 正式发版构建与 GitHub Release SOP

### 步骤 1: 版本号递增与文档对齐
1. 更新 `metadata.yaml` 中的 `version` 字段（如递增为 `v1.7.2`）。
2. 在 `DEVELOPMENT.md` 与 [docs/context/NOW.md](NOW.md) 中登记本次变更。

### 步骤 2: 构建纯净发布资产包
```powershell
# 执行打包脚本，自动排除 .git、__pycache__ 及大型预览图
.\scripts\build_release.ps1
```
*构建产物将输出在工作区根目录下的 ZIP 压缩包。*

### 步骤 3: 发布 GitHub Release
- 使用 GitHub Web 界面或 `gh release create` 创建新版本。
- **发布铁律**: Release 说明严禁包含形如 `pytest 104 passed` 或容器检测耗时等环境调试日志。仅列出对用户有价值的功能更新、外号增补与 Bug 修复。
