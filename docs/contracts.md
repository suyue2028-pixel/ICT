# 后端接入契约 v0.1

包名 `ict-rag-agent`，导入名 `rag_agent`，Python >=3.11,<3.13。部署基线为单机、单进程、一个长期存活的 RAGService 实例；SQLite 不应通过网络文件系统共享。前端只访问后端公开 API。JSON Schema 为本次代码直接导出的 `docs/schemas/*.json`。

## Python 接入

```python
from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput, QueryRequest
from rag_agent.service import RAGService

agent = RAGService(Settings.from_env())

async def accept_collected_batch(items: list[dict]):
    documents = [DocumentInput.model_validate(item) for item in items]
    result = await agent.ingest(documents)
    enrichment = await agent.enrich(result.document_ids)
    return result, enrichment

async def answer_qa(body: dict):
    request = QueryRequest.model_validate(body)
    response = await agent.query(request)
    summary = agent.get_run_summary(response.trace_id)
    # Backend persists response/session and summary, never model private reasoning.
    return response, summary
```

`ingest(documents)` 逐项原子提交，结果包含 inserted/updated/unchanged/failed、document_ids 与 items 的 ID、版本、错误。错误项不会替换有效旧版本；合法项可以独立成功。输入 Pydantic 验证失败应由后端标记采集项错误。

`enrich(document_ids)` 为当前版本构建明确编号/产品关系，幂等；返回 assertions_added/retained、relations_added/retained、candidates、warnings。来源结构字段和受证据约束的 fixed_version 规则在 ingest 时提取，所以此版本 enrich 的 assertions_added 为 0，统计保留数量。语义关系候选尚未生成，candidates 为 0；不代表富化维度已经全面覆盖赛题。

`query(request)` 返回 QueryResponse。缺资料为 insufficient_evidence；缺字段/冲突为 partial；预算或配置问题为 error。查询热路径只访问当前本地快照与配置的 LLM provider，模型文件须先下载到本机。请求取消传递到远程 HTTP；CPU 推理的后台线程无法在中途强制终止，服务层会终止等待，部署时应预热并控制并发。

`get_evidence(evidence_id)` 可读取历史版本证据，未找到抛 KeyError。`get_run_summary(trace_id)` 为进程内最近 128 次运行摘要；只包含节点、工具、证据 ID 和耗时，不代替后端持久化日志。

## DocumentInput

所有时间必须带时区；内部转 UTC。collected_at 必需，published_at/source_modified_at 可空。source_url 必须是 HTTP(S)，不得包含凭据。external_id 可空，此时使用规范 URL；document_id 为 source_name + external_id/URL 的稳定 SHA-256 截断 ID。重复收集的 collected_at 变化不产生版本，正文、标题、结构字段或来源更新时间变化产生新版本。

必须字段：source_name/source_kind/source_url/doc_type/title/text/collected_at。可选字段：external_id/language/published_at/source_modified_at/identifiers/product_entities/ai_scope/risk_category/raw_payload。doc_type 枚举 vulnerability/advisory/paper/standard/policy/blog。无 CVE 的论文/风险指导也可导入。

identifiers 格式 `[{"namespace":"CVE","value":"CVE-2024-37032"}]`；CVE/GHSA 转大写。product_entities 是后端完成别名规范化的明确产品名列表；过滤按不区分大小写的精确名称匹配，别名须一起传入。

NVD 适配器传 `raw_payload={"cve": 原始NVD cve对象}`。保存所有 CVSS 版本和主体，保存完整 configurations 的 AND/OR/negate、嵌套 nodes、边界和 vulnerable 标记。此版本不解释完整 CPE 布尔适用条件，不能宣布某资产受影响。

其他适配器可传 `raw_payload.source_fields=[{"entity_id":"目标编号（可省略）","field":"fixed_version","value":"来源明确提供的值"}]`。支持字段 cvss/affected_version/fixed_version/mitigation/poc_reference。值必须来自采集的来源记录，定位为 `/raw_payload/source_fields/{i}/value`。source_recorded 是忠实记录，不是独立核验。

CVSS value 为 `{version,score,vector,severity,source,type}`，每版本/每来源独立记录。version 必须为2.0/3.0/3.1/4.0，score 必须为0至10的数字；格式错误会令该项导入失败并保留旧版。severity 可缺省，缺省不能满足等级过滤。受影响范围 value 可保留产品/生态、raw_range 和原始边界/条件；该字典不被转换为已确认资产匹配。POC 只记录来源元信息，不执行。

## QueryRequest

```json
{
  "question": "它的评分和修复版本是什么？",
  "history": [{"role":"user","content":"介绍 CVE-2024-37032"}],
  "context": {"identifiers":["CVE-2024-37032"]},
  "filters": {"product":"Ollama","cvss_version":"3.1"},
  "timezone": "Asia/Shanghai"
}
```

history 最多 6 条 user/assistant 消息。事实证据始终重新检索；历史 assistant 文本不作事实或编号来源。context 可提供 identifiers/products，由后端从用户确认的上下文生成。存在多候选时返回澄清。

filters 支持 identifier（最多8个字符串，OR 候选匹配）、product、doc_types、source_kinds、published_from/to、min_cvss_score、cvss_version、severity。问题中的明确编号是目标，各目标单独报告；identifier 过滤只约束这些目标，不引入替代编号。问题未提供编号时，该过滤列表才作为目标。所有显式过滤 AND 组合；字段缺失不能满足过滤，不放宽约束。评分阈值/等级筛选默认 CVSS 3.1；同一条来源评分必须同时满足版本、阈值和等级；任一来源符合则文档可入候选，其他来源断言继续保留。CVSS 3.0/3.1/4.0 不能混合拼分。

“最近”按 timezone/now 的发布时间近30天或明确“近N天”解释；“最近更新”要求澄清，本版本不支持更新时间范围过滤。客户端 now 只用于复现实验，生产公开 API 应移除或用服务器可信 UTC 时间覆盖。自然语言提及的产品和模型规划建议不会悄悄升级为显式过滤；前端筛选应传 filters。

## QueryResponse 与引用

status=ok/partial/insufficient_evidence/error；answer 为中文包装的来源摘录或模型中文 claims；claims=[{text,evidence_ids,kind=fact|inference}]；sources 为 EvidenceDetail 列表；facts 为有 provenance 的 Assertion 列表。另有 missing_fields/conflicts/warnings/trace_id/metrics/mode。

mode 指生成方式 live/mock/extractive，metrics.embedding_mode 单独指向量 live/mock。不得把 extractive 或 mock 展示为真实模型生成。metrics 还包含 elapsed_ms/llm_calls/tool_calls/retries/index_generation。ok 表示本请求正常完成，不是人工事实核验通过。

检索最多8个正文证据块；结构断言有额外字段证据，sources 可多于8条。字段定位是 JSON pointer，正文定位是源 text 的半开字符区间 [start,end)。URL/title/timestamps 从存储读取，模型不能写引用 URL。只允许引用本次选中正文/字段证据。旧 evidence_id 一直绑定旧文档版本，不会被新版本覆盖。

前端解析 claims.evidence_ids，对 sources 建立映射。展示来源、文档版本、定位、发布时间/收集时间、部分回答状态、来源冲突与 needs_review。详情按 evidence_id 请求后端，不把下载目录、数据库路径、密钥或原始模型请求暴露给客户端。

## 后端公开路由建议

| 路由 | 后端职责 |
|---|---|
| POST /api/v1/qa | 认证、可信时间、会话历史、并发/速率限制，调用 query 并持久化回答 |
| GET /api/v1/evidence/{id} | 权限校验，调用 get_evidence，KeyError→404 |
| GET /api/v1/intelligence | 后端自建列表/分页/筛选服务，Agent 不实现 |
| GET /api/v1/jobs/{id} | 采集/富化状态、进度、失败恢复，Agent 不实现 |

演示路由仅 `/demo/query`、`/demo/evidence/{id}` 和 `/health`，默认绑定 127.0.0.1。正式认证、CORS、任务状态、会话存储和公开 API 由后端实现。

## 索引与配置变更

Embedding model_id/revision/dimension/prefix_policy 是索引指纹。变更任何一项必须重建新的数据库，不能把 mock 库当 E5 库。模型 revision 必须是固定40字符 commit。原文、chunks、vectors、assertions 和证据在同一事务写入；更新 generation，读侧矩阵缓存按 generation 失效，一次 query 使用同一个 SQLite 读快照。完整重建在新 DB 导入后由后端切换服务配置，禁止运行时覆盖旧库。

配置错误（如 live 缺 key）在创建服务时抛 ValueError；后端应将此作为部署错误处理。查询内部异常不透传 provider 错误正文或密钥，只输出有限错误类型。schema 版本不兼容拒绝打开，不自动破坏式迁移。
