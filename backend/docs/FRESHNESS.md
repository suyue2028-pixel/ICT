# 情报时效性与最近 48 小时接口

2026-10-08：容器已统一从 `D:\cit\后端开发` 构建，保留此前前端/Agent 兼容改动、DeepSeek、凭据及原数据库卷；不再使用旧后端运行副本。本次未推送 GitHub。

## 自动调度

本机十二个真实适配器已启用。当前已有配置为：OSV、NVD、Cisco、SANS、Hugging Face、Trail of Bits、NVIDIA、Crossref、NIST、GOV.UK、中国政府网每小时；arXiv 每 24 小时。新增来源默认：动态来源每小时，Crossref/固定规范/政策每 6 小时，arXiv 每天。非演示来源间隔上限 48 小时、arXiv 下限 24 小时；旧超长配置实际按 48 小时调度，监控同时报告配置值与实际值。

唯一的本项目 Docker beat 每 5 秒维护计划和 outbox。首次无计划、或恢复后计划过期的来源立即入队，使用行锁/部分唯一索引/租约防止重叠。普通续页 60 秒，Crossref 分页预算暂停 180 秒；续页完成后才等待整个周期。OSV 依次检查所有配置的软件包。失败最多尝试三次，末次失败后按来源指数退避，最长 6 小时；成功清除当前错误，历史失败记录保留。来源游标与原文/版本/证据/知识事件在同一事务提交。

NVD 固定 lastMod 窗口，单窗不超过 120 天，完成后重叠 24 小时，长期掉线分窗补采。Crossref 保存固定 from-index-date/until-index-date 窗口和真正下一页游标，最近 48 小时与历史补采分开推进，新情报不会仅排在几周回填之后；策略切换重新读取旧边界，幂等去重。arXiv 复用更新排序、水位和一天重叠；RSS 使用条件请求。依据：[OSV 分页](https://google.github.io/osv.dev/post-v1-query/)、[NVD](https://nvd.nist.gov/developers/vulnerabilities)、[Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/)、[arXiv 日级缓存](https://info.arxiv.org/help/api/user-manual.html)。

电脑/Docker/网络停止期间不能采集，恢复后补采。RSS 窗口、搜索关键词/页数和固定规范/法规目录均有覆盖边界；来源已移除的文章可能无法补回。成功但无新增是正常结果，不能保证来源每天发布新资料。

## 时间与数据口径

存储统一 UTC、接口带时区；北京时间 UTC+8。

| 字段 | 含义 |
| --- | --- |
| published_at | 来源实际发布时间，未知为 null |
| source_updated_at | 来源实际更新时间；Crossref 是元数据索引更新时间，不等于论文正文更新 |
| first_collected_at | 首次成功入库；历史资料新发现仍不是新发布 |
| Document.last_checked_at | 成功批次中实际观察此记录的时间；304 只更新来源检查时间 |
| Document.content_changed_at | 已存在文档正文/有效结构化元数据变更的入库时间；首次入库不算更新 |
| Document.indexed_at | 当前版本实际索引可查询后的确认时间；新版本先清空，失败不填写 |
| Source.last_attempt_at | worker 真正开始采集时间，入队不等于执行 |
| Source.last_checked_at | 最近采集/解析成功时间，之后入库失败不冒充成功 |
| Source.last_success_at | 最近采集和入库成功时间，包括有效无变化/304 |
| Source.last_new_content_at | 最近入库新增/更新资料时间，可能包含历史或规则无关记录 |

增量迁移 `0006_source_freshness` 只从已有任务恢复可靠的来源执行时间。历史文档的变更/索引/检查时间未知则 null；通用 updated_at 包含规则、检查及索引操作，不能当正文变化。历史版本 Tool 响应不继承当前版本的这些时间。

同一有效内容不重复建版本/事件；原始 HTML/API 包络杂项变化不单独当新情报。来源更新时间较旧的记录不覆盖当前版本，失去租约的旧 worker 也不能提交。产品、证据、原文、版本和原事件均保留。

## API

在 http://127.0.0.1:8010/docs 或浏览器使用以下查询；默认真实范围，ai_relevant=true 是规则筛选，不等于人工确认。

- 发布：`GET /api/v1/documents?ai_relevant=true&recent_hours=48&time_basis=published`
- 来源更新：同接口 `time_basis=updated`。
- 首次发现：同接口 `time_basis=discovered`。
- 本库实际版本变化：同接口 `time_basis=changed`。
- 漏洞情报列表：`GET /api/v1/intelligence?recent_hours=48&time_basis=published`，支持相同参数。
- 自定义范围：time_from/time_to，例如 `2026-10-06T12:00:00Z`；缺时区或范围倒置为 422。recent_hours 为 1—720，省略保持既有行为；旧 published_from/published_to 兼容。
- 公开统计：`GET /api/v1/stats` 增加 recent_48_hours。
- 管理监控：`GET /api/v1/ops/freshness?hours=48`，使用 Swagger Authorize 的 X-API-Key。默认 ai_relevant=true；仅查看无关记录传 false；/ops/metrics 的 real_source_coverage 提供全部来源采集计数。不得向前端交管理密钥。

时间窗口包含下边界、排除未来时间；发布窗口排除只知年/月/日的推断时间，原日期保留在 metadata。监控包含来源最后尝试/检查/成功/新增、下次计划、当前任务、连续失败、错误、stale、待采集任务和当前版本索引积压。stale 按实际周期加宽限判断，日级来源不固定套用六小时阈值。status 可为 disabled/queued/collecting/retrying/error/never_checked/stale/fresh；近期成功但本轮失败可以同时 error、stale=false。

统计分别为 newly_discovered、newly_discovered_and_recently_published、recently_published、source_updated、content_changed，不相加作为总数。发布到首次入库延迟分最近发布/历史回填，缺时间/日期低精度/负值单列；来源更新至实际变更入库另算。轮询周期不是实测延迟，也不是连续两天 SLA 或准确率证明。

## 启停、部署和日志

当前三模块保持已有 Agent 配置：

```powershell
cd D:\cit\ICT-team
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py status
# 仅在未运行时启动；启动本身不调用收费模型
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py start --no-build --mode live
# 只停本机前端/Agent/同步进程，保留 Docker 后端数据库
& D:\cit\.venv\Scripts\python.exe scripts/run_fullstack_windows.py stop
```

源项目升级、测试、受控真实采集：

```powershell
cd D:\cit\后端开发
& D:\cit\.venv\Scripts\python.exe scripts/deploy-timeliness.py
$env:INDEX_PROVIDER='internal' # 仅测试终端，不改 .env 或运行容器
& D:\cit\.venv\Scripts\python.exe -m pytest -q
& D:\cit\.venv\Scripts\python.exe -m alembic check
Remove-Item Env:INDEX_PROVIDER
& D:\cit\.venv\Scripts\python.exe scripts/verify-timeliness.py
& D:\cit\.venv\Scripts\python.exe scripts/verify-timeliness-runtime.py
docker compose logs --tail 80 -f worker beat api
docker compose stop api worker beat
```

部署脚本先 pg_dump、校验归档目录、保留旧应用镜像，再构建本目录、迁移和重建本项目 API/worker/beat。数据卷与 Agent 覆盖配置保留。数据库备份、部署日志、验证报告在被忽略的 output/；不上传凭据、备份或输出。旧镜像回退必须核对与当前迁移兼容，不自动降级/恢复数据库。

本次未制作前端筛选控件；前端队友可按上述参数接入。完整项目入口：http://127.0.0.1:5173；Swagger：http://127.0.0.1:8010/docs。

## 2026-10-08 验证

70 项自动化测试通过，Alembic 无结构差异；十二个真实来源复验成功，29 项 HTTP 检查通过。Crossref 的 HTTP400 曾由本次新日期格式引起：官方实际响应明确不接受时区后缀，改为 UTC 的 yyyy-MM-ddThh:mm:ss 后复验成功，历史失败记录保留。

20:18:33（北京时间）快照：最近 48 小时首次发现规则相关记录 166 份，其中来源发布时间也在窗口内 11 份；来源更新时间在窗口内 36 份；新观测字段记录的实际版本变化 0 份。后台会继续变化，实时值以接口为准。

真实最近发布样例包括 arXiv《AI Safety Considerations for Agents With Limited Time to Act》（2026-10-07T15:49:06Z）、《Package Hallucination Attacks on Coding Agents through Prompt Injection in Rule Files》（2026-10-07T00:59:19Z）；摘要、出处和版本保存在原文接口。这是规则相关性，未声称独立人工准确率。

证据：output/timeliness-live-20261008T121820Z.json；原先失败记录也保留。最终数据保留、beat 实际触发、索引确认和三模块健康记录为 output/timeliness-runtime.json。合成时间/故障测试通过事务回滚，不算真实新增成绩。本次没有再次重启 PostgreSQL，也没有声称已连续观察 48 小时。
