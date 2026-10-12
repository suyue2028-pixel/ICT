# 四小时采集目标（2026-10-12）

## 团队工作区使用

本文件中的 `D:\cit` 路径、数据库数量与运行记录属于原开发机器的验收事实。队友从后端分支取得代码后，在自己的后端目录使用本机 `.env` 和环境，不复制原开发机凭据。团队 Compose 默认项目名为 `ict-backend`，自定义时传入自己的名称：

```powershell
uv run python scripts/ci-check.py --new-db --check-contracts
# 沿用 README 的构建/启动方式；不会自动同步别人的数据库
docker compose up -d --build --wait
uv run python scripts/verify-four-hour-runtime.py --compose-project ict-backend
```

验证脚本使用本机后端配置的端口，`COMPOSE_PROJECT_NAME` 或 `--compose-project` 可指定项目；已配置外部 Agent 的部署也会检查其健康。未配置或未启动 Agent 时，该项会如实失败，不冒充三端联调通过。四小时策略没有新增迁移文件，已有快照迁移沿用工程版本。

## 策略与边界

- 普通真实来源的采集间隔上限为 14,400 秒。已经设置为每小时的来源保持每小时，不降低速度。
- 新增 Crossref、NIST、GOV.UK、中国政府网来源默认 4 小时；其他动态来源默认 1 小时。
- arXiv 是明确例外：相同查询按官方日级更新/缓存说明，每 24 小时执行。不能把该来源算作四小时调度通过。[arXiv API 官方说明](https://info.arxiv.org/help/api/user-manual.html#3311-title-id-link-and-updated)
- 终止失败后的指数退避最多 4 小时；仍保留有限重试、共享限速、唯一 beat、outbox 和防重叠机制。
- API 创建/修改普通来源时，超过 4 小时返回 422 `source_interval_exceeds_4_hours`。arXiv 只允许日级周期；离线演示来源不能启用自动采集。
- 对旧的超长配置，调度执行时按有效上限截取；监控仍分别返回配置间隔与实际间隔。升级已有超长计划时应通过来源管理接口缩短配置并检查 `next_run_at`，不要把尚未到期的旧计划视为已重新排期。

现有部署检查发现 14 个启用真实适配器，其中 13 个已配置每小时，arXiv 每天；本轮无需改写这些更快的周期，也不启用快照导入后默认禁用的来源。

## 查看最近四小时与实际延迟

原有最近 48 小时接口继续兼容。查看最近 4 小时可使用：

```text
GET /api/v1/documents?ai_relevant=true&recent_hours=4&time_basis=published
GET /api/v1/documents?ai_relevant=true&recent_hours=4&time_basis=updated
GET /api/v1/documents?ai_relevant=true&recent_hours=4&time_basis=discovered
GET /api/v1/ops/freshness?hours=48
```

最后一个接口仍需管理员鉴权，不能把管理员密钥交给前端。`schedule_policy` 表示四小时目标、失败退避上限和日级来源例外。每个来源的 `four_hour_schedule_applicable`、`schedule_exception` 明确是否适用。

`publish_to_first_collection_seconds.recent_publications` 增加 `target_seconds`、`within_target_count`、`over_target_count`、`within_target_ratio`，依据来源发布时间与首次入库的实际差值计算；恰好四小时计入目标内。历史回填、时间缺失、低精度日期与负延迟仍单独统计。没有样本时比例为 null。来源更新至本库版本变化的实测统计使用相同目标。

这些统计包含符合筛选范围的实际样本，包括 arXiv 的实际发布延迟；不会为了使结果变好而剔除超时来源。配置周期、检查成功、实际发布延迟是三件不同的事。`delivery_target_verified=false` 表示没有完成连续运行目标验收，不因一次请求成功改写为已通过。

## 运行与验证

入口保持：项目 <http://127.0.0.1:5173>，API <http://127.0.0.1:8010/docs>。沿用现有 Windows 全栈启动器和 Compose overlay，见 [运行说明](OPERATIONS_20261009.md)。没有新增第二个 beat，不修改 .env 或数据卷。四小时策略本身不需要新增迁移；重建镜像时同时应用此前已完成但业务库未执行的 `0010_public_snapshots` 增量迁移，只新增快照来源映射、批次和成员三张表。部署前先备份并验证归档。

从后端目录运行隔离回归检查：

```powershell
$env:INDEX_PROVIDER = 'internal' # 仅当前测试进程环境，不改运行容器或 .env
& D:\cit\.venv\Scripts\python.exe scripts/ci-check.py --new-db --check-contracts
Remove-Item Env:INDEX_PROVIDER
```

只读检查当前运行实例（不会触发付费模型或主动创建采集任务）：

```powershell
& D:\cit\.venv\Scripts\python.exe scripts/verify-four-hour-runtime.py
```

运行结果记录在被 Git 忽略的 `output/four-hour/`。没有等待四小时或四十八小时来冒充持续验收。电脑关机、Docker 停止和来源网络失败仍会造成采集中断，恢复后沿用增量补采。

## 本轮实际验证

北京时间 2026-10-12 20:53:52 完成运行复核：

- 专用数据库 `verify_ci_20261012t124631z_0fedf382`：153 项测试通过，1 项已有 Starlette 弃用警告；Alembic 无结构差异，契约无漂移，Redis PING/API 就绪通过，模型调用为 0。
- 数据库备份为 `output/pre-timeliness-20261012T124848Z.dump`，已通过 `pg_restore --list` 校验；保留了部署前镜像标签。备份与日志不上传。
- 业务库增量升级至 `0010_public_snapshots`，后续 `alembic check` 无差异；原有 2,414 份文档保留。这里只核对文档数量，未把数量一致冒充逐字节数据库校验。
- API、worker、beat 中修改文件的 SHA-256 均与源工程一致；PostgreSQL、Redis、API 健康，Compose 使用既有私密配置可正确解析。
- 本 Compose 项目只有一个 beat；其他独立的早期环境项目不在本轮修改范围。beat 心跳从 2,343 增加到 2,349。
- 将已启用 SANS 的下一次计划提前为现在，未直接创建任务；现有 beat 自动生成任务 `0a2998ad-cd03-4fe1-9ef8-bdac58e4a5e5`，worker 一次执行成功，收到 10 条记录且全部未变化，没有新增或新版本。此验证证明真实调度链路工作，不是新增情报成绩。
- 14 个真实来源保持启用：13 个每小时，arXiv 每 24 小时。未启用或更改离线演示来源。
- 第一次额外 Agent 健康检查未通过，后续直接检查及最终复核均恢复为 200；没有修改 Agent 代码或凭据，保留第一次失败记录。
- 最近 48 小时可靠发布时间样本为 0，四小时达标比例为 null；不能据此证明实测发布延迟已达标。Crossref 仍保留 `source_network_error`，其最后成功记录未覆盖，后续按有限重试/退避恢复。
- 最终运行报告：`output/four-hour/final-result.json`；所有 16 项运行检查通过。敏感文件扫描 164 个公开文件，未发现已配置凭据或常见令牌模式。

以上为原开发机器的开发与部署验证，本次 GitHub 同步保留这些历史事实。上传副本的验证结果另见下方记录；没有制作前端页面或进行服务器部署。

## 上传副本复验

在包含队友最新语义路由、模型平台和动态搜索改动的上传工作区，使用新建专用数据库完成迁移、契约和回归：326 项通过、2 项失败、0 跳过，另有 1 项已有依赖弃用警告。四小时新增回归均通过，迁移无差异、契约无漂移、Redis PING/API 就绪通过。

两个失败与上一批快照上传复验相同，未修改、跳过或弱化安全测试：`test_config_key_file_permissions_unknown_model_and_request_controls` 依赖 POSIX 权限位，Windows 上不能表示相同权限；`test_key_file_private_regular_no_symlink_and_no_double_config` 使用 Windows 缺少的 `os.O_NONBLOCK`。本次不重构队友的密钥文件功能。GitHub Ubuntu CI 的实际结果以本提交的 PR Checks 为准，不能用原工程 153 项结果替代团队全量结果。

本次只同步四小时策略、相关测试、验证脚本及文档，不包含 .env、数据库、备份、运行日志、本地报告或新的公开数据快照。新增验证脚本默认使用团队 `ict-backend` 项目名，并允许显式覆盖。
