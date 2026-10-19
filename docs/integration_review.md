# 三个模块的本地运行与接入检查

检查日期：2026-10-02（Asia/Shanghai）。本次读取并运行三人的源码，保留三人的独立分支。源码版本：

| 模块 | 分支/提交 | 本机位置 |
|---|---|---|
| 前端 | codex/frontend-demo / 1a7c125 | /Users/gshy/Desktop/ICT-RAG/reports/local/integration/frontend |
| 后端 | feature/backend-integration / a33913d | /Users/gshy/Desktop/ICT-RAG/reports/local/integration/backend |
| Agent | agent-dev / 404f7c8 | /Users/gshy/Desktop/ICT-RAG/packages/rag_agent |

前后端是 Git 对象导出的源码快照，没有另建 Git 仓库。依赖、随机本地配置和运行报告位于原本已忽略的 reports/local/ 中。快照版本见 reports/local/integration/snapshots.json。

## 当前运行事实

- 前端使用 Node 24.19.0，按 package-lock.json 安装，监听 http://127.0.0.1:5173/ 。类型检查和生产构建通过；浏览器实测首页、列表翻页、详情、模拟问答及引用跳转成功，所检查的浏览器错误日志为空。页面的数据与问答均为原项目明确标注的演示数据。结果已保存至 frontend_validation.json，原始53个源码文件未变更。
- 后端使用独立 Python 3.12.14 环境，按 uv.lock 安装，API 监听 http://127.0.0.1:8010 。GET /health/live 为200，OpenAPI为200、42个路径。GET /health/ready 为503，postgres/redis/migration均false，情报列表为503 database_unavailable。
- 这台电脑未安装 Docker，也没有 PostgreSQL/Redis 服务；本次没有运行后台worker/beat或完成业务数据库迁移。后端API进程存活不表示采集/会话/问答业务已经就绪。
- Agent 原演示适配器监听 http://127.0.0.1:9000 。GET /health 为200，库中19篇资料；编号查询和中文混合检索均为200。真实E5 CPU工作，回答为extractive，远程LLM调用为0。
- 所有接口实测为真实本机HTTP，结果在 reports/local/integration/runtime_validation.json；没有用HTTP替身或SQLite假装后端基础设施。

## 一、前端还没有向后端发出业务请求

实际代码链为：App.vue:7 调 loadDemo；stores/intelligence.ts:31 从 sampleIntelligence 赋值，图谱和任务直接使用 sample 常量；useDemoChat.ts:31 调 createDemoAnswer，消息标为 simulated。src/api/http.ts 虽然定义了Axios客户端，但没有业务页面调用它。

所以仅设置 VITE_API_BASE_URL 不会接通。前端同学需要增加真实API函数，并更换情报store、问答、任务、统计和图谱的数据来源；演示模式可以独立保留并明确标记。

## 二、前端与后端的地址、模型、会话没有对齐

| 项目 | 前端当前实现 | 后端真实接口/行为 | 需要完成 |
|---|---|---|---|
| 地址 | 默认 localhost:8000 | 本机API 127.0.0.1:8010，统一前缀/api/v1 | 开发Vite代理或明确的CORS；正确API地址 |
| 跨域 | vite.config.ts没有proxy | main.py没有CORS；实测预检405、无allow-origin | 代理/api到后端，或允许指定前端Origin |
| 情报列表 | 本地q筛选、每页5条、slice | GET /api/v1/intelligence，keyword/page/page_size，返回items/total/page/page_size | 明确参数映射，按服务端total分页 |
| 情报字段 | summary/publishedAt/products/tags/evidence等 | 列表VulnerabilityRead只有ID/identifier/title/severity/相关性/范围/入库时间；详情另带documents/evidence | 扩展列表DTO或显式转换；入库时间不能当发布时间 |
| 严重性 | critical/high/medium/low | 来源可unknown；Agent risk_level为大写UNKNOWN等 | 加未知值和明确大小写转换 |
| 问答 | 本地关键词模拟 | POST /api/v1/agent/query，body为query/session_id/is_demo | 调用真实入口，处理partial/insufficient_evidence与错误 |
| 会话 | 当前页面内存，刷新清空 | 后端保存session及消息，/agent/sessions可读取 | 保存并回传session_id，恢复历史 |
| 用户权限 | HTTP客户端无Authorization | 问答需短期用户Bearer；管理员负责签发 | 用户token流程；浏览器不保存管理员/模型/工具密钥 |
| 引用 | intelligenceId，跳漏洞详情 | document_id+content_hash+locator是文档版本证据 | 文档证据入口/ID映射，保留版本，避免误跳漏洞UUID |
| 超时 | Axios 15秒 | Agent网关默认30秒 | 问答使用适当超时与取消/重试，显示稳定错误码 |

后端实际总览为 /api/v1/stats；趋势统计的是采集日期，前端图表当前按样本发布日期/风险绘制，必须先统一统计定义。后端 /api/v1/relations/{vulnerability_id} 只返回漏洞→文档关系，不具备前端演示图中的所有产品/CWE节点。后端 /api/v1/tasks 是采集任务，问答调用记录在 /agent/invocations；前端静态“审核”步骤没有已实现的业务API可直接接入。

代码依据：前端src/App.vue、src/stores/intelligence.ts、src/composables/useDemoChat.ts、src/types/index.ts、src/api/http.ts；后端backend/api/v1/intelligence.py、overview.py、tasks.py、agent.py、auth.py和schemas/contracts.py。

## 三、后端与Agent HTTP协议不兼容

后端 services/agent_gateway.py:30/53/132 要求三个路由：

| 后端要求 | 当前Agent | 本机实测 |
|---|---|---|
| GET /v1/health，schema_version=1/status=ok | GET /health，status=ready | /v1/health返回404 |
| POST /v1/query | POST /demo/query | /v1/query返回404 |
| POST /v1/indexes/verify | 没有索引证明接口 | 返回404 |

把后端原始payload直接发给 /demo/query 也会422：后端发送query/schema_version/session_id/is_demo/request_id/invocation_id/history/limits/state_owner，Agent要求question且extra=forbid。后端历史最多8条并带citations，Agent最多6条且只接受role/content。

响应同样不兼容：后端要求AgentAnswer，必须有schema_version、completed/partial/insufficient_evidence、UUID来源、content_hash、独立evidence、cve_ids、risk_level、retrieval_mode等；当前Agent返回ok/partial/insufficient_evidence/error、claims/facts/sources/mode/metrics。实测当前真实回答不能通过后端AgentAnswer验证，错误包含UUID解析、缺版本哈希和不允许的额外字段。

Agent同学需要实现单独的V1 HTTP Adapter，内部复用RAGService；映射请求/状态/来源/证据/工具摘要，后端error走合适HTTP错误。8次工具上限低于后端传入的10次上限，本身不阻断连接；历史长度、回答预算与超时仍需适配。当前Agent的extractive标识必须继续明确，不能把它展示为远程生成成功。

## 四、三个模块还没有共用同一批知识与版本身份

后端文档ID是UUID、版本为64个十六进制字符的SHA-256 content_hash；Agent独立生成doc_开头ID、整数版本和另一套内容哈希。Agent当前19篇资料来自独立采样，并没有对应后端数据库的文档行。后端会验证返回来源UUID、当前hash、title/URL/source_type/source_name、is_demo以及摘录，见 services/agent_gateway.py:88-121。因此只把字段重命名无法通过来源核验。

应建立持久映射：(is_demo, backend_document_uuid, backend_content_hash) → (rag_document_id, rag_version)。实际从后端精确版本读取并导入，保留原始正文/来源元信息/完整性。discussion/article/regulation等后端类型需明确映射为Agent类型，真实与demo范围必须隔离。字段JSON pointer引用也要与后端的原始字段核验一致，不能把重新排序的JSON当成后端正文原样摘录。

外部索引还缺完整同步链：

1. 后端基础设施就绪，设置INDEX_PROVIDER=external，配置独立读/写/服务凭据。
2. Agent消费者领取 /api/v1/internal/knowledge-events/claim 的带租约事件。
3. 按UUID/content_hash/is_demo从 /api/v1/tools/documents/{id} 读取精确版本。
4. 真实生成向量并原子导入，保存UUID/hash映射，再提供 /v1/indexes/verify 的实际可查询证明。
5. 用有效lease_token和index_id确认ack；不能先确认再补建索引。
6. 删除事件使当前资料不可检索，verify返回queryable=false/removed=true；保留历史证据。当前Agent尚没有删除失效服务。

Agent读工具与领取/确认/富化分别使用后端AGENT_TOOL_TOKEN和AGENT_WRITE_TOKEN；后端调用Agent使用AGENT_SERVICE_TOKEN。随机本地配置已创建但没有共享真实同学密钥，且AGENT_SERVICE_URL当前未配置，后端健康接口实测503 agent_service_not_configured。

## 建议联调顺序与分工

| 顺序 | 负责人 | 可验收结果 |
|---|---|---|
| 1 | 后端/本机环境 | PostgreSQL、Redis、迁移就绪，health/ready=200 |
| 2 | Agent + 后端 | V1适配器、文档身份映射、索引事件与删除失效；health/query/verify真实通过 |
| 3 | 后端 | 用户token、服务token与实际地址配置；明确API示例和错误格式 |
| 4 | 前端 | 接情报列表/详情和agent/query，真实来源、partial/unknown、会话持久化正确展示 |
| 5 | 三人 | 校准总览统计、采集任务和关系图；再做完整链路回归 |

不必先做流式回答、图数据库或重写三套业务。第一轮目标为“同一份后端资料被Agent真实索引→浏览器发问→后端网关→Agent带版本证据回答→前端可点开核验”。远程LLM密钥可最后联调；有来源的extractive链路也应先能端到端运行。

## 本机入口与重新运行

前端已运行并在Codex右侧浏览器打开：http://127.0.0.1:5173/；后端文档 http://127.0.0.1:8010/docs；Agent文档 http://127.0.0.1:9000/docs。当前会话中的后台进程持续提供本机页面，服务停止后可分别在三个终端执行：

```sh
/Users/gshy/Desktop/ICT-RAG/reports/local/integration/start-frontend.sh
```

```sh
/Users/gshy/Desktop/ICT-RAG/reports/local/integration/start-backend.sh
```

```sh
/Users/gshy/Desktop/ICT-RAG/reports/local/integration/start-agent.sh
```

已运行的服务不需要重复启动。后端脚本仅启动API，PostgreSQL/Redis/迁移与worker仍待就绪。实际首页截图保存在 reports/local/integration/frontend-preview.jpg，完整首页在 frontend-home.jpg，模拟问答截图在 frontend-qa.jpg。

HTTP重跑：在 /Users/gshy/Desktop/ICT-RAG/reports/local/integration 执行 `backend/.venv/bin/python runtime_probe.py`，需两套API已启动；它只输出状态与脱敏结构，不输出凭据。Frontend运行结果见 frontend_validation.json，Backend/Agent结果见 runtime_validation.json。
