# 八类证据富化与全库处理

沿用现有八个栏目，为每份非演示、未失效的情报保存当前版本的八条检查结果。完成检查不代表每类都有已知事实；资料缺失与维度不适用明确区分，不编造字段。

## 内容与边界

| 栏目 | 实际处理 |
|---|---|
| 受影响产品与版本 | OSV 软件包/有序范围事件、NVD CPE 与原始边界、厂商明确产品；没有版本时仍明确未知 |
| CWE | 来源明确的 CWE 标识；不凭关键词猜测编号 |
| CVSS | 保留来源向量与分数；缺分数的有效 3.0/3.1 基础向量按公式复算，注明非官方来源分数 |
| 修复信息 | 来源 Patch/Fix 链接和 OSV 明确 fixed 事件；limit 不冒充修复版本，GIT 提交与发布版本分开 |
| 技术分析 | 有出处的分析链接及原文说明/摘要；摘要明确不是新增的独立技术分析 |
| 公开 POC 元数据 | 来源明确的公开链接与标签；不运行 POC |
| 关联论文 | 保存论文版本引用；主题相近的候选始终为待审核，不冒充论文证明了漏洞结论 |
| 公开利用状态 | 来源已有 KEV 字段或严格识别的 NVD 公开利用描述；公开了利用代码不等于已确认实际利用 |

只借用指向**同一个且唯一一个漏洞实体**的记录，最多 12 个关联来源。多 CVE 公告缺乏逐字段归属时不跨文档借用其字段。论文候选扫描至多 500 份当前 AI 相关论文，最多保存两个候选；这是本地已有资料内的候选检索，不能声称查遍全球论文或所有 POC。

每条结果包含来源、文档 ID、版本哈希、Unicode 字符位置、原文摘录、处理方式、审核状态、检查时间、适用性及范围。原始正文、来源字段和历史版本不改写；后端衍生记录使用保留 provider `backend-evidence-v1`，Agent 不能冒用该 provider 回写。

## 状态与接口契约

`GET /api/v1/documents/{id}/enrichment` 保留 `source/external/normalized`，新增 `summary`，始终返回八个栏目：

- `known`：存在来源支持的字段，不等于独立人工审核通过。
- `pending_review`：有引用的候选，仍须审阅关联是否成立。
- `not_found`：已检查保存资料，未找到；不代表外部不存在或系统安全。
- `not_applicable`：该类文档不适用此维度，例如法规没有漏洞 CVSS。
- `unprocessed/queued/processing/failed`：未完成、排队、处理中或失败，不能冒充已检查。

`summary[field]` 带 `state/checked_at/reason_code/scope/applicability`。为兼容旧存储状态，数据库中的不适用记录保存为 `not_found` 加 `value.applicability=not_applicable`，接口 summary 明确转为不适用。

管理员 `POST /api/v1/documents/{id}/enrich` 返回 202 和 `task_id`；刚完成且无需重做时返回 200 `already_processed`。显式 `?force=true` 可重新处理；失败也可通过现有 `/tasks/{id}/retry` 重试。管理密钥仅保存在后端，前端读取接口不需要管理密钥。

管理员 `GET /api/v1/ops/enrichment` 查询全库当前处理器版本的八类进度，默认排除演示。原 `/ops/enrichment-quality` 仍负责原有六维字段结构/审核质量；覆盖率与独立准确率不是同一指标。

## 调度与运行

复用 Celery、Redis、任务步骤、outbox 和唯一 beat。维护任务每轮有上限地派发，按文档版本幂等；每四小时重新检查已保存资料，处理器升级也会重新检查。采集仍使用原来源周期和四小时目标，arXiv 保留日级例外。电脑或 Docker 停止期间不采集，也不声称持续可用。

0011 增量迁移只调整任务互斥索引，让文档富化不占用来源采集互斥；不删数据、卷或历史。worker 最多三次有限重试；近期终止失败不会无限自动制造新任务。旧版本任务不能写入新版本。衍生分数和证据处理不调用付费模型。

进入后端工程根目录，使用现有虚拟环境或 `uv run`：

```powershell
docker compose up -d --build --wait
uv run python scripts/enrich-all.py                   # 只读进度
uv run python scripts/enrich-all.py --queue           # 缺失/过期任务入队
uv run python scripts/enrich-all.py --queue --retry-failed # 修复原因后显式重试未完成项
uv run python scripts/verify-eight-enrichment.py --compose-project ict-backend
uv run python scripts/ci-check.py --new-db --check-contracts
docker compose logs --tail 100 worker beat
docker compose stop
```

验证脚本必须传实际 Compose 项目名。原开发机为 `cit-backend-development`；普通团队配置为 `ict-backend`。验证只读业务数据，结果写入忽略目录 `output/enrichment-eight/`。CI 使用新建 `verify_*` 数据库，不复用业务库做破坏测试。

队友从 GitHub 得到的是代码，不会因此获得原开发机的新数据库。使用各自配置执行迁移、导入已审核的公开快照或自行采集后，再运行富化；不复制任何人的 `.env`、索引数据库或模型密钥。

## 本机复验：2026-10-15

北京时间 13:37 的一致性快照：**2498/2498 篇**真实未失效文档完成八类当前处理器检查，剩余 0。15640 处引用核验通过，12 项运行检查全部通过；api/worker/beat 使用当前代码，唯一业务 beat，0011 已应用，outbox→worker 真实完成。

| 栏目 | 有证据记录 | 待审核 | 未找到 | 不适用 |
|---|---:|---:|---:|---:|
| 产品与版本 | 1458 | 0 | 111 | 929 |
| CWE | 1414 | 0 | 155 | 929 |
| CVSS | 1448 | 0 | 121 | 929 |
| 修复信息 | 1347 | 0 | 222 | 929 |
| 分析/原文说明 | 2498 | 0 | 0 | 0 |
| POC 元数据 | 33 | 0 | 1536 | 929 |
| 论文关联 | 0 | 213 | 2285 | 0 |
| 利用状态 | 1 | 0 | 1568 | 929 |

利用状态的 1 篇是来源报告公开利用代码，不是已确认在野利用。论文 213 篇是有原文引用的候选，尚未人工确认。以上数值会随采集变化；它们不是全库人工准确率、全球召回率或连续 48 小时 SLA。

修复后的后端源工程 203 项自动化测试通过，另有一个既有依赖弃用警告。原失败任务记录保留，本次修复和重新处理没有删除历史失败来美化成绩。当前复验不包含新的真实生成式问答质量评测；DeepSeek 配置和既有问答功能保留。


团队合并版及前端验证见[本次复验记录](UPGRADE_VALIDATION_20261015.md)，不能用源工程203项结果代替包含队友功能的全量测试。
