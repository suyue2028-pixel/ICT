# 后端与 Agent/RAG 联调契约 V1

统一 API 前缀 `/api/v1`，schema_version 为字符串 `"1"`，时间为带时区 ISO 8601，ID 为 UUID，版本为 64 位 SHA-256。实时 OpenAPI：<http://127.0.0.1:8010/docs>；导出文件 `docs/openapi.json`、`docs/contracts/`。

本契约是后端已实现的接口及**拟交给队友实现的服务协议**。Agent 服务尚未运行，未声称双方已完成真实联调。后端保存数据、证据、会话、事件及调用审计；队友负责 RAG 切片/向量/重排、工具选择、回答、跨文档推理。保留的内置能力不与外部消费者同时抢事件。

## 1. 网络与权限

后端容器 API 的本机地址为 `http://127.0.0.1:8010`；从其他容器访问的地址取决于网络配置，不能把 localhost 当作宿主机。Windows 上的 Agent 可访问此本机地址，后端容器访问 Windows Agent 使用 `http://host.docker.internal:9000` 等实际端口，或正式 HTTPS 服务。

管理员为 `X-API-Key`。Agent 只读工具为 `X-Agent-Token: AGENT_TOOL_TOKEN`；索引确认/富化写入为独立 `AGENT_WRITE_TOKEN`。二者只配置在服务端，只读令牌不能执行写接口。后端调用 Agent 以 AGENT_SERVICE_TOKEN 作为 Bearer（如配置）。兼容旧管理调用，但不要给 Agent/前端管理密钥。

普通用户持短期 Bearer：管理员 `POST /auth/tokens` 请求 `{"owner_id":"developer1","expires_in_hours":24}` 签发；仅一次显示 token、数据库保存 SHA-256，可 `DELETE /auth/tokens/{id}` 撤销。每分钟默认 10 次、每天 100 次，失败调用也计入预算。历史只允许同一 owner 查询。模型/数据库/管理密钥不进入浏览器。

## 2. Agent 入口与会话

`POST /agent/query`，Authorization Bearer，可选 `Idempotency-Key`（≤128 字符）：

```json
{"query":"这个 CVE 影响哪些版本？","session_id":null,"is_demo":false}
```

返回 `AgentQueryResponse`：schema_version、status（completed/partial/insufficient_evidence）、answer、sources[]、cve_ids[]、risk_level（UNKNOWN/LOW/MEDIUM/HIGH/CRITICAL）、evidence[]、tool_calls[]、tool_trace[]、partial_errors[]、retrieval_mode、session_id、invocation_id、request_id。

sources 每条：document_id、content_hash、title、url、source_type、source_name、published_time。evidence 每条：document_id、content_hash、content（原文摘录）、source（URL）、locator、可选 score/score_type。score 是余弦或相关性分数，不能解释为事实置信度。完成/部分回答必须有可核验来源和摘录；内容必须来自当前版本、范围一致，CVE 必须实际关联到引用文档。无依据时使用 insufficient_evidence，不能补造回答。

同一用户、相同 Idempotency-Key 和请求体返回同一次成功结果；不同请求体返回 409；进行中重复请求返回 409，同会话并发互斥。失败重试使用新 key。单会话最多 100 轮，每轮只传最近 8 条消息（单条截断到 2000 字符），max_tool_calls=10、max_answer_tokens=1200 是传给 Agent 的执行预算，Agent 必须自行落实模型调用上限。

`GET /agent/sessions`、`GET /agent/sessions/{session_id}`、`GET /agent/invocations/{invocation_id}` 使用用户 Bearer。后端是**会话状态唯一保存方**；Agent 返回的每轮只保存一次。tool_trace 提供工具名、input_summary、duration_ms、状态、error_code 和 document_ids；持久化会脱敏。轨迹由 Agent 上报并经 schema 校验，不能称后端独立证明了工具执行，也不请求模型私有思维链。

## 3. 队友服务须实现的 V1 接口

设置本地 AGENT_SERVICE_URL（仅 HTTPS 或明确宿主机/loopback 开发 HTTP），不要删除现有 DeepSeek Key。后端不会启动一个虚假 Agent。

| 队友接口 | 请求/响应 |
|---|---|
| `GET /v1/health` | 返回 `{"schema_version":"1","status":"ok"}`；管理员可通过后端 `GET /agent/health` 实际探测 |
| `POST /v1/query` | 接收下面的 payload，返回严格 AgentAnswer JSON，不额外嵌套 data，不自行保存后端会话 |
| `POST /v1/indexes/verify` | 返回指定文档/版本/范围在真实索引中的可查询性；delete 须确认失效 |

`/v1/query` 接收 schema_version、query、session_id、is_demo、request_id、invocation_id、history[]（role/content/citations）、limits、state_owner="backend"。输入文档和历史是数据，不能改变工具权限或系统策略。返回版本必须显式为 `"1"`。后端请求超时默认 30s，响应上限 500KB，不跟随重定向；未配置 503 `agent_service_not_configured`、超时 504 `agent_timeout`、不可达 502 `agent_unavailable`、无效内容或引文 502。工具部分失败可用 partial 并列 partial_errors，仍须真实证据。

该入口是 `/api/v1/agent/query`，没有另建 `/api/agent/query` 兼容别名。队友应按本文件/API schema 对齐；如其已有不同协议，后续仅改 Adapter，不复制 Agent 工作流。

## 4. Tool 映射与数据

所有 `/tools/*` 用只读凭据，响应为 `{"schema_version":"1","request_id":"...","status":"completed|empty","data":...}`，分页 data 为 items/total/page/page_size，最大 100。固定函数/API：

| 协作函数 | HTTP |
|---|---|
| search_cve | `GET /tools/cves?query=...`，CVE/别名/标题搜索 |
| get_cve_detail | `GET /tools/cves/{CVE}`，本地显式别名关联、CVSS/字段证据和多方原文 |
| search_nvd | `GET /tools/nvd?query=...`，确实 adapter=nvd，不用 OSV 冒名 |
| search_vendor_advisory | `GET /tools/vendor-advisories?query=...` |
| search_source_documents | `GET /tools/documents?query=...&document_type=paper`（可 standard/regulation 等） |
| read_document | `GET /tools/documents/{id}?content_hash=版本`，可读保留的历史版本 |

默认真实范围；演示需显式 is_demo=true。失效文档不能被 Tool 或问答检索；审计可通过原 `/documents/{id}` 查看保留历史和 deleted_at。每条文档带 document_id/hash、raw_id、source_name/source_url/source_type、adapter、document_type、发布时间/来源更新/首次采集时间、元数据、正文完整性、规则相关性。历史版本读取的 title/URL/内容来自该版本。缺失为 null，论文摘要不是全文，元数据不能冒充语义结果。

缓存 last_checked_at 是来源最后成功检查的时间，version_updated_at 是该文档变更时间；stale 按 6h 表示，仅供查询方决定是否触发刷新，非真实性置信度。刷新通过管理员 `/sources/{id}/collect` 返回 202 和任务 ID，同步 Tool 不抓远程数据。CVE 聚合 severity 保留历史最高来源评级，各版本原文/证据可核对冲突。

统一错误：HTTP 401（鉴权）、403（会话归属）、404（无本地记录）、409（版本/并发）、422（结构/证据）、429（预算），JSON `{"error":{"code":"稳定码","message":"..."},"request_id":"..."}`；不是所有空集合都报错，搜索为空是 200 empty。

## 5. 知识更新、索引确认与失效

文档 add/update 产生 KnowledgeEvent。逻辑删除 `DELETE /documents/{id}` 返回 202、产生 delete，保留所有原文/版本/字段证据；采集不会自动恢复管理员失效的文档。事件身份为 document_id+content_hash+operation。

`INDEX_PROVIDER=internal`：beat/Celery 处理内置索引。只有当前版本的真实 EmbeddingChunk 入库、模型/维度匹配，才允许 indexed。没有独立 Embedding 配置时 pending，不发送假向量。内置删除使当前向量不可检索并标 invalidated，历史向量保留审计。

`INDEX_PROVIDER=external`：禁用内置索引派发，由队友使用写凭据 `POST /internal/knowledge-events/claim`，请求 `{"is_demo":false,"limit":5}`（≤20），返回 items 中 event_id、document_id、content_hash、operation、is_demo、lease_token、lease_until、document_url。租约 15 分钟，SKIP LOCKED 防止争抢；过期最多 3 次，随后 failed。旧版本标 superseded，不能覆盖新版本。delete 不需要再下载已失效正文，只按 ID/版本删除检索记录。

索引确实写入后，用写凭据 `POST /internal/knowledge-events/{id}/ack`：

```json
{"status":"indexed","lease_token":"从 claim 获得的 UUID","detail":{"index_id":"真实索引标识"}}
```

后端调用队友 `/v1/indexes/verify` 请求：schema_version、document_id、content_hash、index_id、is_demo、operation。upsert 响应必须逐项相同且 `queryable:true`。delete ack 状态用 invalidated，证明响应 `queryable:false, removed:true`。任意 index_id 或只写 ack 都不能证明成功；外部模式不要求队友重复写本地 EmbeddingChunk。

失败回写 status=failed、有效 lease_token、detail.error_code；到期租约不能覆盖状态。相同已确认结果重复 ack 幂等。`GET /knowledge-events` 查询状态；管理员 `/knowledge-events/{id}/retry` 重试 failed；`/knowledge-events/{id}/replay` 202 重放当前版本或 delete，拒绝处理中的事件和已过时版本。切换 provider 前等待/恢复旧租约，确保唯一消费者。

## 6. 富化回写

写凭据 `POST /internal/enrichments`：document_id、当前 content_hash、provider、fields。维度 affected/cwe/cvss/remediation/analysis/papers/poc/exploitation，格式见 schemas。known 必须有结构化 value 和真实 evidence；unknown/unprocessed/failed/pending_review 不代表已知。

证据包括 document_id/hash/source_url/locator/excerpt，允许同一范围的其他**当前版本**文档支持关联，但必须核验原文，不因主题相近自动认定论文对应某个 CVE。后端记录 source_reported 与 external:provider 分开；外部 payload 保留 extraction_method 和 review_state，model 方法标 pending_review，不将模型断言解释为人工审核事实。相同 provider/版本/字段相同结果幂等，矛盾 409，其他提供者可并存保留冲突。POC 仅公开元数据，不执行。

## 7. 保留的内置/旧问答

`/qa/search` 返回关键词原文，不生成答案。`/qa/semantic-search` 需管理凭据，真实向量缺失时不足或未配置 503。`/qa/ask` 保持 question/document_ids/conversation_id，默认现有 DeepSeek；`/qa/conversations` 保护旧管理会话，仅传最近 8 条历史。内置语义是 PostgreSQL 数组精确余弦，不是 pgvector；降级标明 keyword/keyword_fallback。

旧单轮外部服务为 AI_SERVICE_URL/v1/ask，使用 QuestionResponse；`QA_PROVIDER=external` 显式选择该兼容路径，不删除 Key。新的多轮普通用户网关使用 AGENT_SERVICE_URL/v1/query，二者契约不能混用。

## 8. 无前端联调顺序

先管理员签发用户 token；队友配置自己的 Tool/写入凭据，提供上述 health/query/verify；后端设置 AGENT_SERVICE_URL 并重建配置；启用 external index provider 后 claim→读取版本→真实索引→verify→ack；用户 query→保存 session→追问→查询历史和轨迹。脚本 `scripts/agent-client-example.py` 演示用户端两轮，绝不携带管理或模型密钥。

本轮 HTTP 测试替身覆盖契约、超时、错误引用、租约和幂等，只用于测试，不进入生产接口。真实队友联调、真实语义检索、模型质量验收仍待服务与凭据。
