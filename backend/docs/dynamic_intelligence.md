# Agent 按需官方情报补查接口

## 功能边界

这是受内部服务鉴权保护的有限情报查询目录，用于 Agent 根据当前问题与缺失证据补查官方资料。它不提供任意网页搜索、任意 URL 抓取、Shell 执行或用户指定的工具定义。所有资料先经过现有清洗、原始修订保存与证据生成流程，再进入 Agent 索引；接口返回本次真正取得或缓存的资料。

本接口只适用于真实资料范围。Agent 的 demo 请求不注册外部补查执行器；导入前再次检查返回记录 `is_demo=false`、工具/实体与原问题一致。合成测试资料只出现在 Mock 测试中。

## 鉴权与请求

`POST /api/v1/internal/intelligence/lookup` 使用 `X-Agent-Token` 对应的 `AGENT_WRITE_TOKEN`，由 `require_internal_write` 校验。普通用户 Bearer Token和只读工具令牌不能调用；兼容的管理员凭据仅限服务器管理员。浏览器不会取得这些内部凭据。

请求是严格 Pydantic 对象，禁止额外字段，支持以下工具及受限参数：

| `tool` | 必填参数 | 校验和用途 |
| --- | --- | --- |
| `nvd_lookup` | `identifier` | 标准 `CVE-YYYY-NNNN…` 形式，仅查询该编号 |
| `kev_lookup` | `identifier` | 同上，仅在本次取得的 CISA KEV 目录查匹配条目 |
| `osv_lookup` | `ecosystem`、`package`、`version` | 生态仅 `PyPI/npm/Go/Maven/crates.io`；包名、版本使用受限字符且每项最多100字 |
| `knowledge_lookup` | `topic` | 仅 `agency/injection/poisoning`，对应预注册OWASP条目 |

例如：

```json
{"tool":"nvd_lookup","identifier":"CVE-2023-36258"}
```

```json
{"tool":"osv_lookup","ecosystem":"PyPI","package":"langchain","version":"0.0.200"}
```

代码不会从模型记忆猜测软件包生态、包名或版本；缺少这些参数时应要求用户补充。请求结构不接受 `url`、`base_url`、API Key、任意工具名或命令。

## 固定来源、缓存与限流

| 工具 | 固定来源 | 成功结果缓存TTL |
| --- | --- | --- |
| NVD | `https://services.nvd.nist.gov/rest/json/cves/2.0`，参数 `cveId`、`resultsPerPage=1` | 900秒 |
| CISA KEV | `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` | 900秒 |
| CISA镜像 | 仅主目录不可用时使用官方 `cisagov/kev-data` GitHub develop目录 | 与KEV共用TTL |
| OSV | `https://api.osv.dev/v1/query`，明确包与版本 | 900秒 |
| OWASP Agency | `https://genai.owasp.org/llmrisk/llm062025-excessive-agency/` | 3600秒 |
| OWASP Injection | `https://genai.owasp.org/llmrisk/llm01-prompt-injection/` | 3600秒 |
| OWASP Poisoning | `https://genai.owasp.org/llmrisk/llm042025-data-and-model-poisoning/` | 3600秒 |

HTTP不跟随重定向，也不读取操作系统代理设置。一次fetch含重试与CISA镜像共用8秒截止时间；仅对瞬时502/503/504、连接或超时故障最多重试一次，429不立即重试。总响应体上限3 MB，失败不写缓存。

进程缓存最多128个查询键，键包含实际工具参数。缓存命中保留原来的 `checked_at`，不会把命中时间伪装成重新抓取时间。NVD最少6秒一次，其余工具最少0.5秒一次；既有Redis可用时额外进行跨进程限流，不可用时保留本地限流。限流是工具服务保护，仍须遵守 Agent 自身的每次工具次数、轮数、时间与用户请求预算。

## 响应与错误

成功或真实无匹配响应：

```json
{
  "schema_version": "1",
  "tool": "kev_lookup",
  "status": "empty",
  "documents": [],
  "checked_at": "本次实际成功读取目录的时间",
  "cache_hit": false,
  "limitation": "kev_no_entry_is_not_proof_of_no_exploitation"
}
```

`completed` 表示返回了保存后的记录；`empty`仅表示本次受限查询无匹配，不表示产品安全、没有漏洞或从未被利用。KEV要求有效且有时区的目录发布日，未匹配条目明确保留“不能证明未被利用”的限制。

故障与无匹配严格分开：

| 错误 | HTTP/处理 |
| --- | --- |
| `intelligence_timeout` | 504；不生成无匹配事实 |
| `intelligence_rate_limited` | 503；保留失败状态 |
| `intelligence_source_unavailable` | 503；不伪造来源，也不当成目录无条目 |
| `intelligence_invalid_response` | 502/503（解析阶段不同）；禁止畸形响应成为空查询 |
| `intelligence_entity_mismatch` | 503；返回的CVE或包实体不符时拒绝导入 |
| `intelligence_document_body_missing` | 503；OWASP正文未能确认时拒绝资料 |
| `intelligence_response_too_large` | 503；不导入超限正文 |
| `intelligence_tool_unavailable` | 503；拒绝未注册工具 |

供应商任意错误体、请求凭据、堆栈不会作为公开错误信息转发。未知参数由现有请求错误机制返回422。

## 原始修订、事实与索引

1. 将官方响应转换为真实 `RawRecord`，保留官方更新时间、评分主体、原始影响条件、出处以及正文完整性。
2. 通过现有 `ingest_batch` 写入 `Document`、`DocumentRevision`、`Evidence` 和 `KnowledgeEvent`，保持原始修订哈希、去重与原文核验规则。
3. 返回保存后的 `document_json`、当前原始修订payload、正文、版本时间以及 `is_current=true`。
4. Agent检查工具、原问题实体和demo范围，使用现有 `BackendIndex.upsert` 导入，再读取当前scope检索。普通索引事件同步仍可按现有流程完成；事件和查询结果必须真实可检索后才能宣称已索引。
5. 最终引用继续绑定真实Document、修订哈希、来源URL与原文片段，后端再独立校验。查到资料不能代替答案支持核验。

NVD保留各CVSS版本与评分主体，不取最大值冒充唯一评分；CPE布尔条件仍未求解，不能直接判断用户资产。OSV保留原始ranges/events，不自动推导“某版本之前均受影响/修复边界以上均安全”。OSV本次最多8条且只处理受限首屏，分页或结果截断明确标注。OWASP保存解析到的正文，超过30000字符标记partial，不伪装完整全文。

`checked_at`是实际查读时间，官方记录更新时间或KEV目录发布日另行保存。缓存命中与来源旧版本均必须保留相应时效限制。

## DataSource与调度器

补查创建名为 `Agent on-demand <tool>` 的内部DataSource，始终 `enabled=false`，并保存 `kind=agent_lookup` 的任务审计记录。这些DataSource用于出处、版本和审计，不代表已经配置了通用采集器。尤其 `kev/owasp` 的按需实现不能冒充现有普通采集器注册表的能力。

不要为了让页面显示“已接入”而启用这些内部DataSource或更改adapter。现有采集调度器、采集频率、其他真实DataSource与资料不会由本接口自动启用、删除或替换。

## 验证边界

新增测试使用Mock HTTP验证固定地址、严格式、实体、缓存时间、重试、限流、失败与无记录区分。业务数据库回归使用既有事务回滚测试。单测通过不表示真实NVD/CISA/OSV查询成功；真实查询及返回原始资料、索引可检索与带引用回答须在全栈验收记录中分别报告。
