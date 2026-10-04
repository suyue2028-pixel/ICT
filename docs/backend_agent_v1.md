# 后端与 Agent HTTP V1 接入

更新日期：2026-10-04（Asia/Shanghai）。依据 GitHub `suyue2028-pixel/ICT` 的 `feature/backend-integration` 分支提交 `a33913da1b63dbc638e2cbff266aabc970833597`，10月3日经 GitHub 连接核对，该分支与此前快照一致。

本接口供业务后端调用。前端继续访问后端的 `POST /api/v1/agent/query`；用户认证、会话、请求幂等和调用记录由后端维护。独立 `/demo/*` 路由仍供原有资料演示使用。

## 启动与密钥对应

在仓库根目录的一个终端中设置环境并启动：

```sh
source .venv/bin/activate
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"
export HF_HUB_OFFLINE=1
export RAG_BACKEND_DB_PATH="$PWD/data/backend-rag.db"
export RAG_LLM_MODE=extractive
# RAG_SERVICE_TOKEN 通过本机私有环境配置，须与后端一致。
python -m uvicorn examples.backend_api:app --host 127.0.0.1 --port 9001
```

服务与同步 worker 必须使用同一个 `RAG_BACKEND_DB_PATH`、同一嵌入模型指纹。本机原演示服务可继续使用 9000 端口、`data/ict.db`。V1 默认是独立 `data/backend-rag.db`，不会把独立演示19篇资料当成后端已有文档。

| 用途 | Agent 环境变量 | 后端对应变量/行为 |
|---|---|---|
| 后端调用 Agent | RAG_SERVICE_TOKEN | AGENT_SERVICE_TOKEN；请求头 Authorization: Bearer |
| Agent 读取后端资料 | RAG_BACKEND_TOOL_TOKEN | AGENT_TOOL_TOKEN；请求头 X-Agent-Token |
| Agent 领取/确认知识事件 | RAG_BACKEND_WRITE_TOKEN | AGENT_WRITE_TOKEN；请求头也为 X-Agent-Token |
| 后端地址 | RAG_BACKEND_URL=http://127.0.0.1:8010 | 原生本机后端地址 |
| Agent 地址 | V1 监听9001 | AGENT_SERVICE_URL=http://127.0.0.1:9001 |
| 外部索引模式 | 同步worker | INDEX_PROVIDER=external |
| 大模型生成 | RAG_LLM_KEY / RAG_LLM_KEY_FILE | Agent 自己的供应商凭据，不能拿服务令牌代替 |

如果后端在容器运行，需按容器网络配置实际可访问的 Agent 地址。后端源码允许 `http://host.docker.internal:9001`；`localhost` 指向调用方自身。服务当前绑定回环地址，任何跨机器部署都应另行配置网络与 HTTPS。

没有配置 `RAG_SERVICE_TOKEN` 时，V1 业务路由返回503；错误或缺失 Bearer 返回401。密钥不进入 Git。模型密钥配置见 [大模型 API 本地配置](llm_configuration.md)。

## 三个路由

| 路由 | 请求 | 结果 |
|---|---|---|
| GET /v1/health | 服务Bearer | schema_version=1/status=ok；生成模式、嵌入模式、两类文档数量 |
| POST /v1/query | BackendQuery | AgentAnswer，与当前后端严格模型兼容 |
| POST /v1/indexes/verify | IndexVerification | 后端UUID/hash/scope/index_id对应的真实索引证明 |

query原样接收后端的 schema_version/query/session_id/is_demo/request_id/invocation_id/history/limits/state_owner。history允许8条，核心只接最近6条role/content，历史引用和旧助手回答不作为新事实证据。`state_owner` 必须为backend，身份不由模型生成。

回答字段：schema_version、status、answer、sources、cve_ids、risk_level、evidence、tool_calls、tool_trace、partial_errors、retrieval_mode。status为completed/partial/insufficient_evidence；核心错误映射HTTP429/503/504，不伪装成正常回答。

source保留后端UUID、SHA-256 content_hash、title、url、source_type/source_name、published_time；evidence保留该UUID/hash与原文摘录。locator为`content:chars:start:end`，或`revision_json:chars:start:end`，起止采用Python Unicode字符索引（end不包含）；后者对应`json.dumps(revision, ensure_ascii=False)`序列化结果，不指向Agent内部的转换结构。每条摘录须出现在该版本正文或后端保存的revision JSON中。不合格引用被撤下，相关回答重建为可核验摘录或资料不足。没有真实相似度时score=null、score_type=not_scored。

CVE只从最终正文提及且来源revision记录的别名/主编号输出。风险等级只取有效证据支持的已记录CVSS severity，缺失为UNKNOWN。规则抽取、来源记录和独立安全核验不是同一件事。

模式通过partial_errors和 `X-RAG-Answer-Mode`、`X-RAG-Embedding-Mode`、`X-RAG-LLM-Calls` 标记；后端当前不会向前端透传这些header，因此页面至少应读取partial_errors中的模式标识。后端不允许额外mode字段，Agent不在正文契约中擅加字段。

工具事件来自本次真实调用的名称、状态、耗时和文档UUID。请求总时限取本地配置与25秒中的较小值；工具预算取本地8次与后端上限中的较小值。远程生成max_tokens不超过后端预算。V1最终正文采用同值的保守UTF-8字节上限，优先保留完整行，截断为partial并标记answer_budget_truncated；这不是供应商tokenizer的精确计量。

## 文档同步与版本身份

持久映射：`(is_demo, backend UUID, backend content_hash) → (RAG document_id, RAG version, index_id)`。真实／demo范围独立；查询只允许当前活跃映射的精确RAG版本。

BackendIndex.upsert接收后端精确版本工具响应data，保留content/revision、原始来源元信息和content_completeness。discussion/article映射为blog，regulation映射为policy；原document_type仍保存，映射不代表把摘要扩充成全文。导入时使用本地真实E5，不能拿mock向量给后端确认真实索引。

在另一个终端配置读/写令牌与后端地址，执行一次同步：

```sh
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"
export HF_HUB_OFFLINE=1
export RAG_BACKEND_DB_PATH="$PWD/data/backend-rag.db"
export RAG_BACKEND_URL=http://127.0.0.1:8010
# RAG_BACKEND_TOOL_TOKEN/RAG_BACKEND_WRITE_TOKEN 通过本机私有环境配置。
.venv/bin/python scripts/sync_backend.py --once
```

演示范围使用 `--is-demo`。不加 `--once` 按有限间隔轮询；脚本负责消费索引事件，持续多源采集仍由后端负责。

同步顺序：claim租约→读取UUID/hash/is_demo精确版本→真实生成向量并导入→保存映射→索引证明可查→用lease_token/index_id确认indexed。失败记录failed；租约过期、409或无法确认ACK不算成功。事件提供的document_url不作为任意网络目标。

删除顺序：当前资料失效→验证queryable=false/removed=true→确认invalidated。历史版本和证据保留。旧哈希事件不能删除新版本；重复导入/重复确认须保持幂等。

首次同步前已删除、没有本地映射的事件使用独立缺席证明：实际检查该UUID/scope不存在活跃映射或未失效的孤立导入；每次verify重新检查。后续资料导入会使旧缺席证明失效。具体验证范围与未完成项见本轮验证记录。

## 本次范围与后端仍需完成的接入

Agent代码、独立验证和真实端到端联调是不同验收项。本机后端目前还缺PostgreSQL/Redis/迁移；因此不能把本地契约样本通过解释为真实后端文档已经同步。

后端同学接入顺序：基础服务ready→配置三类令牌及Agent URL→INDEX_PROVIDER=external→采集产生事件→worker同步→后端自身来源核验通过→前端接真实问答。前端的代理/CORS、用户token和字段转换仍由前后端同学完成。

本次验证结果另见 [Agent V1 验证记录](agent_v1_validation.md)。模型中文生成质量、人工引用支持率和跨文档推理正确率仍需真实模型及人工评测。

## 本机整链补充（10月4日）

真实前端、PostgreSQL/Redis/Celery后端已接入，运行与26项HTTP结果见 [fullstack_local.md](fullstack_local.md)、[fullstack_validation.md](fullstack_validation.md)。同步可用 `scripts/sync_backend.py --all-scopes` 在同一E5进程依次消费两个隔离范围；默认仍只消费真实范围，`--is-demo`仅消费虚构范围。不能同时指定两种选项。
