# 自动关联、回答溯源、CI 与恢复

本轮沿用现有项目、数据库卷、DeepSeek 和外部 Agent/RAG。没有改前端或复制 Agent 索引实现，也没有上传 GitHub。

## 自动关联与历史版本

增量迁移 `0009_association_evidence` 新增 `association_evidence`。每条依据固定到 document_id、content_hash、identifier，保存分组、关联方法、原始字段定位和原值。

- CVE/GHSA/PYSEC/OSV 显式标识优先；只有漏洞记录明确声明的别名才作为同一组。
- 文章、厂商公告中同时提到的不同 CVE 分别关联，不因此合并漏洞实体。
- 没有标准标识的漏洞沿用精确产品/规范化正文或精确 URL 的保守局部指纹，接口明确标记 fallback。
- 正文更新后按新版本重建当前关联，移除当前文档的旧关联边；旧版本和旧依据保留。聚合严重度仍是历史来源最高值，当前来源证据另查。
- 迁移不猜测旧记录的“当时关联过程”。旧文档查询时可由其已保存版本重建依据，标记 `reconstructed_from_revision`、recorded_at=null；新入库依据为 `recorded_at_ingestion`。
- 论文、政策主题相近不能自动证明漏洞关系。本轮不将尚无明确证据的跨主题语义推理标成确认关系。

## 新增接口

| 接口 | 权限 | 内容 |
|---|---|---|
| GET `/api/v1/documents/{id}/associations?content_hash=...` | 沿用原文档公开读权限 | 版本内关联依据、原字段位置、当前有效边、依据来源 |
| GET `/api/v1/documents/{id}/versions/{hash}` | 沿用原文档公开读权限 | 指定历史原文、完整版本 JSON、确定的 JSON 文本表示、关联依据、当前/失效状态 |
| GET `/api/v1/agent/invocations/{id}/evidence` | 用户 Bearer，限调用所有者 | 保存的回答、生成模式、引用版本、原文偏移、关联依据、目前观察到的索引事件 |
| GET `/api/v1/relations/{vulnerability_id}` | 原权限兼容 | 关联边新增版本地址和依据；过滤没有当前版本依据的旧边 |

问答请求 `/agent/query` 及 Agent V1 契约保持不变。引用先核对文档/版本/URL/原文，再核对 locator 指定区间；错误位置即使包含真实摘录也拒绝。支持 `content:chars:start:end`、`revision_json:chars:start:end` 和旧版字段名 `content`/`revision_json`。

位置为 **Unicode code point**，右端不包含。`revision_json` 的表示固定为 Python `json.dumps(payload, ensure_ascii=False)`，接口直接返回其文本。JavaScript UTF-16 的 `slice` 不能直接使用含 emoji 的 code point 偏移；可以 `Array.from(text).slice(start,end).join('')`。

证据接口将历史版本继续作为审计证据保留；更新/逻辑失效后 is_current=false，不把旧证据叫当前资料。旧回答缺生成模式时返回 unknown。`index_events_observed_now` 是查询时状态，不冒充回答生成时的索引快照。

`quote_verified` 只证明摘录与版本/URL/位置一致。`claim_support_status=not_independently_reviewed` 不代表已人工证明每条结论正确。工具轨迹仍是 Agent 上报并经校验、脱敏的轨迹。

## CI

配置位于 `.github/workflows/backend-ci.yml`；本地等价入口为 `scripts/ci-check.py`。

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts/ci-check.py --new-db --check-contracts
```

每次 `--new-db` 创建新的 `verify_ci_*` 验证数据库并保留，不在现有业务库上跑测试。CI 仅允许 ci_backend/verify_* 数据库；迁移、Alembic 模型差异、Redis PING、API 就绪、Python 编译、OpenAPI/契约漂移、公开文件凭据检查和完整 pytest 都检查，保存JUnit报告。模拟 HTTP 用例不调用付费模型。

GitHub job 使用 Python 3.12、固定 uv 0.12.19 和 uv.lock；Action 固定 commit SHA、contents:read、不持久化 checkout 凭据。独立 PostgreSQL/Redis service 用一次性 CI 配置，不使用本机 `.env`。然后解析 Compose、构建 Docker 镜像，并运行下述隔离故障演练。

**GitHub 仅识别仓库根 `.github/workflows/`**。将来同步到团队仓库 backend/ 时，需要把该 workflow 文件放到仓库根 `.github/workflows/backend-ci.yml`，其自动识别 backend/ 或独立项目根。不是放在 backend/.github/ 后就会运行。当前只完成本地文件和等价检查，尚未推送/触发 GitHub Actions，也未设置分支保护。

官方依据：[checkout 固定版本](https://github.com/actions/checkout/tree/11bd71901bbe5b1630ceea73d27597364c9af683)、[setup-python 固定版本](https://github.com/actions/setup-python/tree/a26af69be951a213d495a4c3e4e4022e16d87065)、[uv GitHub Actions 指南](https://docs.astral.sh/uv/guides/integration/github/)。

## 故障演练和恢复

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts/verify-fault-recovery.py
& 'D:\cit\.venv\Scripts\python.exe' scripts/verify-recovery.py
& 'D:\cit\ICT-team\.venv\Scripts\python.exe' 'D:\cit\ICT-team\scripts\verify_index_recovery.py'
```

第一个命令创建 `verify-fault-*` 独立 Compose 项目、随机凭据、新 PostgreSQL/Redis 数据卷和随机本机 API 端口，仅使用虚构样本，不启动 beat。实际检查：

1. Redis 停止时 API 仍以 202 将任务/outbox 持久保存，发送失败不推进游标。
2. Redis 恢复后 outbox 补发、worker 完成入库。
3. 同一幂等键得到同一任务，重复导入不新增版本。
4. 停止 worker，先发送消息，等待 AOF 落盘，正常重启 Redis，再启动 worker，消息仍能处理。
5. 正常重启独立 PostgreSQL，文档 hash 和关联依据保留。
6. 无新增调度实例。结束时只 stop 演练项目，保留验证卷、容器和本机报告，不删除原有卷。

发现的发布等待问题已修正：outbox 任务结果在 TaskRun/KnowledgeEvent 中保存，发送时 `ignore_result=True`，避免发送前进入无用的 Redis 结果订阅重连；`retry=False`，失败继续由 outbox 退避重试。测试单独限制故障发布等待在20秒内，实际耗时记录在 result.json；不能据此宣称生产 RTO/SLA。

第二个命令通过 repeatable-read + pg_export_snapshot 取一致快照，pg_dump custom 备份后恢复到 **新的 verify_restore_* 数据库**；对比每张表行数、SQL行内容指纹、迁移头，并对恢复库运行真实 API 文档查询。原库不覆盖、不停采。

第三个命令只读打开生产 SQLite 索引，online backup 到新文件，逐表计数和 quick_check；原索引不替换。模型缓存、云端凭据和整个宿主机故障恢复仍需单独演练。

备份回退原则：先保留当前数据库和索引的独立副本；只向新库恢复，然后在隔离环境核验新库。迁移后不能直接把0008旧镜像切到0009数据库；现有启动器会拒绝迁移头不匹配的回退。新增迁移不自动 downgrade、不删审计历史，优先修正代码后前向部署。保留备份不等于已完成整机灾备。

## 报告位置和保密

本地验证输出在 `output/ci/`、`output/reliability/verify-fault-*/`、`output/traceability/demo-*/` 和 `output/recovery-*`。这些文件、备份、测试库、索引与本机日志不提交。演示脚本临时用户 token 不输出、不落文件，结束时撤销。数据库恢复副本含原库私密数据，按原库权限保管。
