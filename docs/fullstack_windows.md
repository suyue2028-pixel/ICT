# Windows 三模块运行说明（2026-10-06）

2026-10-08 更新：后端现从 D:\cit\后端开发 构建，保留本页所述联调兼容能力，并部署新的 0006_source_freshness。启动器默认使用源项目，旧固定副本仅作历史参考。当前十二来源真实采集及最近 48 小时接口见 D:\cit\后端开发\docs\FRESHNESS.md；下方“源代码未覆盖”是 10 月 6 日操作记录，源项目现已继续改进。当前修复整理到 feature/agent-qa-relevance 分支；下方旧记录保留原阶段范围。

本机已实际跑通前端 → FastAPI 后端 → Agent/RAG → DeepSeek，并验证真实 E5 语义混合检索、原文版本引用、连续追问、PostgreSQL 会话保存及鉴权。请从本机页面进入：

| 服务 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5173 |
| 问答 | http://127.0.0.1:5173/qa |
| 后端 Swagger | http://127.0.0.1:8010/docs |
| 后端就绪检查 | http://127.0.0.1:8010/health/ready |
| Agent | http://127.0.0.1:9001/v1/health（需要服务令牌） |

## 代码与运行目录

此次从 GitHub 拉取三个已有分支，保持各分支独立，未合并或推送：

| 模块 | 分支及提交 | 本机运行代码 |
| --- | --- | --- |
| 前端 | codex/frontend-demo / 06889e9 | D:\cit\ICT-team\reports\local\integration\frontend |
| 后端 | feature/backend-integration / ee78ebf | D:\cit\ICT-team\reports\local\integration\backend |
| Agent | agent-dev / 9daad1c | D:\cit\ICT-team\packages\rag_agent |

前后端副本由 `scripts/export_windows_modules.py` 从固定提交导出，位于 Git 忽略目录；脚本拒绝覆盖已有目录。各模块修改仍需回到其负责分支审查提交。原 `D:\cit\后端开发` 源码和 `.env` 未覆盖。后端 Docker 镜像使用上述最新 GitHub 副本构建，原 PostgreSQL、Redis、beat 持久化卷继续复用。

后端本地 Python 环境继续使用 `D:\cit\.venv`；Agent 使用单独的 `D:\cit\ICT-team\.venv`（Python 3.12.14，符合 Agent 的版本范围），避免改变后端依赖。PyTorch 为官方 CPU 版本，没有安装 CUDA 或本地生成式大模型。E5 固定 Hugging Face 提交为 `614241f622f53c4eeff9890bdc4f31cfecc418b3`，首次下载已完成；启动会先验证真实 384 维向量，正常运行离线读取缓存。

## 启动、停止及日志

Docker Desktop 需先运行。在 PowerShell 执行：

```powershell
cd D:\cit\ICT-team
# 已构建的本机环境：启用现有 DeepSeek 真实生成
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py start --no-build --mode live
# 查看本次启动器管理的服务状态
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py status
# 停止此次新增的前端、Agent 与同步进程；保留原 Docker 后端和数据库
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py stop
```

首次构建或前后端运行副本更新后，去掉 `--no-build`；启动会先备份现有 PostgreSQL，保存原应用镜像标签，再构建后端。已运行时不能重复启动；脚本检查 5173、9001 端口，不结束其他占用程序。停止操作核对本次记录的 PID 和进程创建时间，防止终止其他进程。只有一个 Docker beat，Windows 不再运行额外 Celery worker/beat。

仅使用来源摘录、避免远程模型调用时，使用 `--mode extractive`；切换模式前先执行 `stop`。`live` 模式会按每请求最多 3 次模型 HTTP 请求、已有每日/每分钟限制调用 DeepSeek，可能产生该账号正常 API 用量。默认命令选择 `extractive`，本次交付保留实际验证过的 `live` 模式运行。

日志：`D:\cit\ICT-team\reports\local\windows-fullstack\logs`。查看：

```powershell
Get-Content reports/local/windows-fullstack/logs/agent.log -Tail 30
Get-Content reports/local/windows-fullstack/logs/sync.log -Tail 10
Get-Content reports/local/windows-fullstack/logs/frontend.log -Tail 20
```

前端的普通用户令牌有效期为 24 小时，只在浏览器内存和被忽略的本地 session 文件中使用。到期后执行以下命令并刷新页面：

```powershell
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py token
```

模型密钥、数据库密码、管理员和 Agent 读写令牌继续由原本地 `.env` 读取；新增 Agent 服务令牌保存在被忽略的 `.secrets/windows-integration.env`。不得提交 `.env`、`.secrets`、session 文件、虚拟环境、缓存、SQLite 索引、日志或数据库备份。前端没有管理员或模型密钥。

## 实际验证记录

- 后端 52 项测试通过（真实 PostgreSQL，专用测试行事务回滚）；Alembic 当前为 `0005_document_invalidation`，模型与迁移无差异。
- Agent 72 项回归通过；首次发现 Windows 缺少 IANA 时区数据，已复用已有官方 tzdata 2026.4 并补充 Windows 条件依赖。回归中的测试替身不计入真实生成成绩。
- 前端类型检查、生产构建通过。
- 摘录模式 30/30 项真实 HTTP 检查通过：同源代理、数据接口、普通用户认证、跨域令牌拒绝、外部索引核验、精确与真实语义混合检索、版本/定位/摘录核对、幂等、连续追问、4 条会话消息保存、真实/虚构隔离和无证据处理。
- DeepSeek 实际调用的两轮验证 4/4 通过：第一轮约 3.55 秒，真实生成响应头为 `live`，每轮实际模型 HTTP 请求 1 次；重复幂等请求返回同一结果；会话保存 4 条消息。本轮结果为 `partial`，包含回答预算裁剪提示，追问另有缺失来源字段提示；不代表独立答案质量评分通过。
- 原有 870 份真实资料保留；229 份已标记 AI 相关的真实文档进入实际 E5 索引，另有 1 份虚构资料独立索引。未入索引的非 AI 相关或旧版本事件不能按索引失败计数。
- 数据库变更前备份已通过 `pg_restore --list` 检查；仅记录一个 Docker beat 实例。
- 额外运行验证 10/10 通过：重复索引不增加文档数量、索引仍可查询、其他用户无法读取会话、测试令牌撤销、应用重启后的会话保留、真实 Celery worker PING 和原资料保留。该验证没有再次重启 PostgreSQL 服务。
- 浏览器已实际检查总览、问答、连续追问、引用跳转及服务重启后的历史恢复。更多持久化和重复索引结果见 `reports/local/windows-fullstack/validation-runtime.json`。

详细机器可读记录位于 `reports/local/windows-fullstack/validation-extractive.json`、`validation-live-smoke.json`。重复验证命令：

```powershell
# 先以 extractive 模式运行；此脚本也检查实际语义检索
& D:\cit\.venv\Scripts\python.exe scripts/verify_fullstack_windows.py
# 先以 live 模式运行；执行两轮真实 DeepSeek 调用
& D:\cit\.venv\Scripts\python.exe scripts/verify_live_windows.py
# 不调用模型；检查重复索引、鉴权和运行状态
& D:\cit\.venv\Scripts\python.exe scripts/verify_runtime_windows.py
```

历史消息目前没有独立保存生成模式，页面会显示“历史消息 · 生成模式未记录”；未知结构字段保持未知，部分回答会显示相应提示。生产登录、公开部署、高并发和独立评分评测不属于本次本机联调结论。

## 回退

原始源码、配置、数据库卷均保留，首次升级前应用镜像标签为 `cit-backend-development:pre-windows-integration`。恢复原应用镜像和 Compose 配置：

```powershell
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py restore-backend
```

回退不删除数据库卷、不降级迁移、不回滚业务数据。升级前备份在被忽略的 `reports/local/windows-fullstack/pre-integration-*.dump`；恢复备份属于另行审查的数据恢复操作，本次没有执行。
