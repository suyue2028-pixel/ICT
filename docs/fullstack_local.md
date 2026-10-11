# 三模块本机联调（2026-10-04）

前端 → 业务后端 → Agent 已在本机实际跑通。后端使用 PostgreSQL/Redis/Celery，Agent 使用固定版本 E5 本地向量与 SQLite 版本索引。默认回答为来源摘录，不需要模型密钥，也不会产生模型 API 费用。

## 当前电脑直接使用

工作目录为 `/Users/gshy/Desktop/ICT-RAG`。前端代码在 `reports/local/integration/frontend`，后端代码在 `reports/local/integration/backend`，Agent 在仓库根目录。前后端是各自分支的本地运行副本；修改分别提交到团队对应分支。运行副本、依赖、数据和秘密配置被 Git 忽略。

```sh
cd /Users/gshy/Desktop/ICT-RAG
.venv/bin/python scripts/run_fullstack.py status
# 已运行时直接打开 http://127.0.0.1:5173
# 停止后再次启动：
.venv/bin/python scripts/run_fullstack.py start --detach
# 只重启 Agent，其他服务和已有数据保留：
.venv/bin/python scripts/run_fullstack.py restart-agent
# 停止本项目服务，保留数据库和索引：
.venv/bin/python scripts/run_fullstack.py stop
```

省略 `--detach` 会在终端监督服务，Ctrl-C 会停止本项目子进程。停止脚本按 PID 与项目命令身份检查，只处理它记录的项目进程；若进程还未退出则保留管理状态。启动失败会清理本次已启动的进程。

`restart-agent` 执行完就返回，不需要 `--detach`。它只替换记录中的 Agent 进程，复用现有前端、业务后端、PostgreSQL、Redis、Celery worker/beat 和同步 worker；不执行数据库迁移、资料采集或 `seed`。需要 `processes.json` 中记录的 Agent 仍属于本项目，且已有运行副本和秘密配置完整。源码更改会由新 Agent 加载；前后端源码不会因此更新。

| 服务 | 本机地址/端口 | 用途 |
|---|---|---|
| 前端 Vite | `http://127.0.0.1:5173` | 页面；同源 `/api` 代理 |
| 业务后端 | `http://127.0.0.1:8010` | 采集、资料、认证、会话、Agent 网关 |
| Agent | `http://127.0.0.1:9001` | 带服务认证的 V1 查询与索引核验 |
| PostgreSQL | `127.0.0.1:5440` | 真实资料、任务、用户会话、调用记录 |
| Redis | `127.0.0.1:6380` | Celery 队列、后端协调 |

另有 Celery worker、唯一 beat、Agent 同步 worker，共八个进程。端口占用时启动报错，不抢占既有服务。默认数据库、端口和固定安装路径由此脚本管理，请不要单独修改 `.secrets/fullstack.env` 的地址字段。

日志：`reports/local/fullstack/logs/`；状态：`processes.json`。数据库在 `.cache/fullstack/postgres-data`，Redis AOF 在 `.cache/fullstack/redis-data`，Agent 索引在 `data/backend-rag.db`。不要通过删除这些目录来重启服务。

### 前端更新与运行副本（2026-10-07）

GitHub 分支更新后，本机已有的运行副本不会自动替换；`start` 会保留已有源码。更新时先检查并备份本地修改，再同步对应前端分支源码并重启 Vite。只刷新浏览器或执行 `git fetch`，都不会替换这个被 Git 忽略的运行副本。

本机前端现已同步到 `codex/frontend-demo` 的 `5741bdb43c60983456b2e68fb62f4149b99183b6`，新增服务状态栏、证据展示、版本提取信息和图谱工具栏。页面整体布局延续原版；可通过顶部“后端 · 正常 / Agent · 正常”和知识事件数量识别新版。

启动器为新版 Vite 状态桥提供实际后端与 Agent 地址，并生成 `.secrets/fullstack-agent-status.env`（0600）。该文件仅含已有 Agent 服务令牌，由 Vite 服务端读取，不传到浏览器。新版页面已在浏览器确认显示：47 个真实索引事件，待处理 0，失败 0；本次没有重新运行完整回归测试。

## 新电脑准备

前提：Git、Python 3.11（Agent）、Python 3.12（后端）、Node 24.12+（本次使用 24.19）、npm、C 编译器和 make。先取回三个分支；仍然分别维护，不合并到 main。

```sh
git fetch origin agent-dev codex/frontend-demo feature/backend-integration
git switch agent-dev
# 在没有运行副本时导出到被 Git 忽略的目录。
mkdir -p reports/local/integration
git archive origin/codex/frontend-demo frontend | tar -x -C reports/local/integration
git archive origin/feature/backend-integration backend | tar -x -C reports/local/integration

python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation packages/rag_agent
.venv/bin/python scripts/download_model.py
python3.12 -m venv reports/local/integration/backend/.venv
reports/local/integration/backend/.venv/bin/python -m pip install --require-hashes -r reports/local/integration/backend/requirements.txt
cd reports/local/integration/frontend
npm ci
cd ../../../..
.venv/bin/python scripts/setup_local_services.py
.venv/bin/python scripts/run_fullstack.py start --detach
```

导出只在目标目录不存在时执行；已有副本应由负责同学保留修改后更新。也可通过 `ICT_BACKEND_DIR`、`ICT_FRONTEND_DIR` 指定现有工作副本。源码构建脚本从官方地址下载 PostgreSQL 16.15 和 Redis 7.4.10 并校验 SHA-256，安装到项目 `.cache`，没有系统服务安装。当前编译配置服务于回环地址的本机联调，正式部署请按后端部署文档准备数据库服务。

首次采集及验证：

```sh
.venv/bin/python scripts/run_fullstack.py seed
# 在页面「运行状态」等待采集完成，再等待同步日志完成 ACK。
.venv/bin/python scripts/verify_fullstack.py
```

`seed` 提交有限公开源任务：OSV langchain 包、NIST、国内政策，并单独导入明确标记的虚构样本。采集会访问互联网，返回量和可用性可能变化。计划采集默认关闭，避免演示产生无界请求。同步 worker 使用 `--all-scopes`，两种范围分别租用事件、索引和 ACK；不向互联网重新搜索。非 AI 相关资料按后端规则不入 Agent 索引。

验证要求至少一个真实 CVE 的当前版本已 indexed。若只有标准/政策或采集未完成，脚本会说明前置条件不满足。脚本在查询前要求 Agent 为 extractive 模式；不会把 live 测试报告成零次模型调用。

## 凭据与模型

启动器生成独立数据库密码、Redis 密码、管理员凭据以及三类服务令牌，保存到 `.secrets/fullstack.env`（0600）。前端仅获得后端签发的 24 小时普通用户令牌，保存在浏览器内存。`/__local/session` 仅用于本机 Vite 开发服务，校验 Host/Origin、禁止缓存，不提供管理员或模型密钥。生产页面需要团队登录流程。

令牌过期可运行 `.venv/bin/python scripts/run_fullstack.py token` 后刷新页面。会话 ID 按真实/虚构范围分别存入 sessionStorage，聊天正文和证据持久保存于业务后端。

已有团队 DeepSeek 密钥可以复用，不需要每位同学购买独立账号。密钥仅配置于服务器；Agent 检索与最终生成负责主问答链，后端旧 `/qa/ask` 路径独立存在，主 `/agent/query` 不会重复调用它。

配置方式见 [大模型配置](llm_configuration.md)。确认有可用密钥后：

```sh
# 密钥由团队成员私下配置到 .secrets/llm-api-key，无需粘贴到聊天中。
# 显式设置 RAG_LLM_KEY 或 RAG_LLM_KEY_FILE 时，二者只能选其一。
.venv/bin/python scripts/run_fullstack.py restart-agent --live
.venv/bin/python scripts/run_fullstack.py status
# 回到不调用供应商的来源摘录模式：
.venv/bin/python scripts/run_fullstack.py restart-agent
# 所有服务尚未运行时才使用完整启动：
.venv/bin/python scripts/run_fullstack.py start --live --detach
```

live 配置的密钥选择顺序：显式 `RAG_LLM_KEY` 或 `RAG_LLM_KEY_FILE`，已有项目 `.secrets/llm-api-key`，最后是后端副本 `.env` 中的 `DEEPSEEK_API_KEY`。默认文件不存在时不设置它；显式文件无效时不会静默切换其他密钥。模型默认 `deepseek-flash`，base URL 默认 `https://api.deepseek.com`；显式 `RAG_LLM_MODEL`、`RAG_LLM_BASE_URL` 会保留。官方 DeepSeek 接口关闭 thinking。凭据只传入 Agent 进程环境，不打印、写入进程状态或命令参数。同步 worker 不接收模型凭据。

完整启动和单独重启共用同一份 Agent 环境构建逻辑。单独重启会先做配置与密钥文件格式预检，再停止旧 Agent；预检没有供应商请求。启动后用带服务认证的 `/v1/health` 确认进程归属及实际 `llm_mode`。`AGENT READY ... generation=live` 只证明服务加载 live 配置，供应商认证、额度和回答质量仍需要另外的实际问答验收。

替换失败时会仅清理本次 Agent，尝试使用当前配置恢复原生成模式。恢复失败则保留其他服务，将状态记录为 `unavailable` 或 `stopping_failed`，命令返回失败；不会把它报告为 ready。日志仍在 `reports/local/fullstack/logs/agent.log`。2026-10-10 已实际重启 live Agent 并完成真实模型问答，见 [问答运行记录摘要](qa_evidence_improvement.md)；失败回滚分支未进行本轮故障注入验证。此前旧版非 detach 终端监督进程不会自动加载新脚本，使用单独重启前应确认本机服务来自 detach 启动；本版监督循环会跟随新的 Agent 登记。

## 接入契约

| 方向 | 接口 | 认证/关键字段 |
|---|---|---|
| 前端 → 后端 | `POST /api/v1/agent/query` | 普通用户 Bearer；`query/session_id/is_demo`；可选 `Idempotency-Key` |
| 后端 → Agent | `POST /v1/query` | `AGENT_SERVICE_TOKEN`；V1 query、后端会话历史、预算 |
| Agent → 后端 | 知识事件 claim、当前 revision 读取 | 专用只读 tool token；真实/虚构范围分别处理 |
| Agent → 后端 | ACK/索引确认 | 专用 write token；UUID/hash/index_id/operation；后端独立 HTTP 核验 |
| 前端 → 后端 | `GET /api/v1/agent/sessions/{id}` | 相同用户；消息 `id/created_at/role/content/citations/retrieval_mode` |
| 前端 → 后端 | documents/tasks/intelligence/stats | 统一 `is_demo`；分页 `items/total/page/page_size`；来源列表独立 |

完整 V1 服务字段、租约、删除和错误契约见 [backend_agent_v1.md](backend_agent_v1.md)。查询响应保留 `completed/partial/insufficient_evidence`、`UNKNOWN`、后端 UUID、原始 content_hash 和定位。后端转发白名单 `X-RAG-Answer-Mode`、`X-RAG-Embedding-Mode`、`X-RAG-LLM-Calls`。没有模式信息时页面显示未知，不推测模型已调用。

文档列表额外返回 `excerpt`、`source_reported_severity`、`source_products`、`source_aliases`，均从当前已保存资料派生。来源详情返回 `revisions`；引用链接携带 `content_hash`，历史版本找不到时报错，不静默展示当前版。历史会话暂未保存独立生成模式字段，页面会明确标记“历史消息 · 生成模式未记录”。

## 实际验证与未验收范围

见 [基础联调报告](fullstack_validation.md) 与 [2026-10-10 问答运行记录摘要](qa_evidence_improvement.md)。本机已有 47 份真实资料，来源为 OSV 45、NIST 1、国内政策 1；47 个真实知识事件已 indexed。另有 3 份虚构样本，其中 1 份 AI 相关样本进入独立 demo 索引。

基础链路已联通，真实 Flash 问答已运行。Flash 稳定性与完整质量评测、独立人工答案评测、更多渠道持续采集、复杂 CPE 适用性、资产匹配、生产登录与部署、高并发和比赛提交材料仍需继续完成；本机联调通过不代表这些已验收。
