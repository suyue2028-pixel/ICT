# 三模块本机运行与联调（更新于 2026-10-12）

当前链路为 **Vue → 业务 FastAPI → Agent/LangGraph → 真实工具与 RAG → 后端统一模型服务 → 引用核验 → 页面**。后端使用 PostgreSQL、Redis 和 Celery；Agent 使用固定版本 E5 本地向量及 SQLite 版本索引。平台默认模型保留 DeepSeek Flash，普通用户授权后可直接问答，不配置供应商 API Key。

## 当前电脑直接使用

工作目录为 `/Users/gshy/Desktop/ICT-RAG`。前端运行副本在 `reports/local/integration/frontend`，后端在 `reports/local/integration/backend`，Agent 源码在 `packages/rag_agent`。前后端副本、依赖、数据、日志和私密配置被 Git 忽略；团队仍分别维护前端、后端、Agent 分支。

```sh
cd /Users/gshy/Desktop/ICT-RAG
.venv/bin/python scripts/run_fullstack.py status

# 全部服务停止后启动；默认平台 live 模型，需要后端已配置可用 Key。
.venv/bin/python scripts/run_fullstack.py start --detach

# 仅替换 Agent。须先确保业务后端已包含并运行新的内部接口。
.venv/bin/python scripts/run_fullstack.py restart-agent --live

# 明确切换为不调用大模型的来源摘录模式。
.venv/bin/python scripts/run_fullstack.py restart-agent --extractive

# 停止本项目服务，保留数据库与索引。
.venv/bin/python scripts/run_fullstack.py stop
```

页面地址为 `http://127.0.0.1:5173/qa`。不带 `--detach` 的完整启动会监督服务，Ctrl-C 停止本项目子进程。`restart-agent` 执行完成后直接返回，不需要 `--detach`；它复用其余七个进程，不运行迁移或资料采集，也不会更新运行中的前后端源码。

启动器默认 `live`；`--extractive` 只关闭模型调用。按需外部情报查询是独立开关，**不调用模型仍可能访问官方情报源**。完全限定为本地已有资料时，显式设置：

```sh
RAG_EXTERNAL_TOOLS_ENABLED=false .venv/bin/python scripts/run_fullstack.py restart-agent --extractive
```

启动与替换均检查配置、进程归属和端口。完整启动失败会清理本次启动的进程；Agent 替换失败会尝试恢复此前模式，恢复失败时保留其他服务并记录不可用状态。不要删除数据库或索引目录来重启。

| 服务 | 本机地址／端口 | 用途 |
|---|---|---|
| 前端 Vite | `http://127.0.0.1:5173` | 页面与同源 `/api` 代理 |
| 业务后端 | `http://127.0.0.1:8010` | 用户认证、资料、会话、Agent 网关、统一模型及情报接口 |
| Agent | `http://127.0.0.1:9001` | 服务认证的 V1 查询、工作流、引用与索引核验 |
| PostgreSQL | `127.0.0.1:5440` | 资料版本、任务、用户会话与调用记录 |
| Redis | `127.0.0.1:6380` | Celery 队列与限流协调 |

另有 Celery worker、唯一 beat 和 Agent 同步 worker，共八个进程。日志在 `reports/local/fullstack/logs/`，进程登记在 `reports/local/fullstack/processes.json`。PostgreSQL 数据在 `.cache/fullstack/postgres-data`，Redis AOF 在 `.cache/fullstack/redis-data`，Agent 索引在 `data/backend-rag.db`。

## 用户授权与会话恢复

当前真实实现是**管理员签发短期普通用户令牌 + 用户授权页面**，尚未实现账号密码注册登录或自动续期。打开 `/login` 可填写已签发的用户令牌；页面使用 `GET /api/v1/auth/me` 验证身份，通过后返回问答。输入的是用户访问凭证，不是模型 API Key；令牌只留在页面运行内存，不写入前端配置或浏览器持久令牌存储。

本机完整启动会签发 24 小时普通用户令牌，私密保存到 `reports/local/fullstack/session.json`。Vite 开发桥 `/__local/session` 只读取已有有效令牌，不签发令牌；它检查 Host／Origin、禁止缓存，不提供管理员或模型密钥。

授权过期时，问答页提供“重新授权”入口并保留草稿和失败问题。草稿与失败问题按真实／demo 范围分别保存在 sessionStorage；恢复授权后可重试或放回输入框。聊天正文、会话和引用由业务后端持久保存。

本机管理员重新签发令牌：

```sh
.venv/bin/python scripts/run_fullstack.py token
```

随后在授权页点击“重新获取本地用户授权”。这只恢复管理员已签发的凭证，不绕过用户鉴权。刷新页面后，本机开发环境可以再次读取并验证有效的本地令牌；生产页面需重新提交有效用户凭证。

用户会话无效与供应商 Key 错误、模型超时、限流、内部服务认证失败分别处理。模型错误不会引导用户反复重新登录。前端保持黑白视觉风格，没有模型 API Key 输入框或未实现的模型切换按钮。

## 平台模型与按需情报查询

供应商 Key **仅由业务后端读取**。配置为 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 或 `LLM_API_KEY_FILE`；兼容团队已有 `DEEPSEEK_API_KEY` 和 `DASHSCOPE_API_KEY`。完整配置见 [llm_configuration.md](llm_configuration.md)。

启动器可以把旧 `RAG_LLM_KEY(_FILE)` 配置迁移到后端，或把已有 `.secrets/llm-api-key` 的路径交给后端；启动器不读取该文件内容。Agent 使用 `RAG_LLM_TRANSPORT=backend` 和内部服务令牌，Agent、同步 worker、前端与 Celery worker 的进程环境会去除供应商 Key 和密钥文件路径。Vite 状态桥另有 `.secrets/fullstack-agent-status.env`（0600），仅由 Vite 服务端读取 Agent 健康令牌，不交给浏览器。

切换平台 Provider 或密钥，需要重启业务后端；只运行 `restart-agent` 不会改变后端已加载的配置。live 预检读取后端配置并检查内部模型 capability，不发送付费模型请求。`READY` 或 `/v1/health` 证明配置和进程已加载，真实供应商认证、额度、生成与核验结果需另外发送问答验收。

**完整启动器在没有显式环境变量时默认开启外部工具。** 独立 Agent `Settings` 与根 `.env.example` 保持 `RAG_EXTERNAL_TOOLS_ENABLED=false`，便于离线开发；显式设置的 false 优先于启动器默认值。需要动态查询时可设置 true 后重启 Agent。

可用的注册外部工具包括 NVD 编号查询、CISA KEV 目录查询、OSV 显式包／版本查询，以及固定 OWASP GenAI 主题资料。它们通过后端内部接口执行，先进入现有资料版本与索引流程，再参与检索和引用，不能由模型提供任意 URL。包名、生态或版本缺失时澄清；KEV 无条目不能推断没有利用；源超时或错误如实披露。

最新信息问题会按需求缺口补查真实来源，并报告检索轮次、工具、需求支持情况和限制。使用缓存时保留原核查时间，不改成当前时间。普通问题继续优先本地资料；工具注册、次数、轮次和时间预算限制补检索。

## 新电脑准备与补丁顺序

前提：Git、Python 3.11（Agent）、Python 3.12（后端）、Node 24.12+、npm、C 编译器和 make。先取回三个分支；分别维护，不合并 main。

```sh
git fetch origin agent-dev codex/frontend-demo feature/backend-integration
git switch agent-dev
mkdir -p reports/local/integration
git archive origin/codex/frontend-demo frontend | tar -x -C reports/local/integration
git archive origin/feature/backend-integration backend | tar -x -C reports/local/integration
```

以上导出仅适用于目标副本目录尚不存在的情况。已有目录先备份并保留本地修改，不用重新导出覆盖。也可用 `ICT_BACKEND_DIR`、`ICT_FRONTEND_DIR` 指定现有完整工作副本。

2026-10-12发布的三个完整分支已包含当前实现，使用这些分支源码时不必重复应用下面的历史增量补丁。补丁仅用于明确的较早基线迁移，不能覆盖同学的新提交。

启动器保留已有运行副本，也不会自动安装接入补丁。**先准备对应分支源码，依次应用 conversation、dynamic、semantic 补丁**，才能获得平台模型、语义分类、情报接口、用户授权页和前端需求展示。分支已经包含某个补丁时，反向检查通过后跳过；任何正向检查失败都先解决源码差异，不继续安装。

```sh
# 在 Agent 仓库根目录执行，路径前缀指向刚导出的运行副本。
# 每个阶段补丁基于前一阶段，不能跳过中间阶段。
for patch in integration/backend_patches/conversation_v1.patch \
             integration/frontend_patches/conversation_v1.patch \
             integration/backend_patches/dynamic_agent_v1.patch \
             integration/frontend_patches/dynamic_agent_v1.patch \
             integration/backend_patches/semantic_intent_v1.patch \
             integration/frontend_patches/semantic_routing_v1.patch
do
  if git apply --reverse --check --directory=reports/local/integration "$patch" 2>/dev/null; then
    continue
  fi
  git apply --check --directory=reports/local/integration "$patch" || break
  git apply --directory=reports/local/integration "$patch" || break
done
```

检查六个补丁都已应用后再安装依赖；若使用自定义工作副本路径，按补丁中的 `backend/`、`frontend/` 前缀调整应用目标。仅复制 Agent 源码或创建空的前后端目录无法运行完整系统。通用搜索还需要后端 `TAVILY_API_KEY` 或私密 `.secrets/tavily-api-key` 文件，缺失时明确返回搜索未配置；DeepSeek Key 不等于搜索 Key。

```sh
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
# 在服务器私密位置配置模型 Key 后：
.venv/bin/python scripts/run_fullstack.py start --detach
```

源码构建脚本从官方地址下载 PostgreSQL 和 Redis，校验 SHA-256 并安装到项目 `.cache`，不安装系统服务。GitHub 分支更新不会自动替换运行副本；`git fetch`、刷新页面或重启 Agent 都不会更新前后端副本。同步源码并应用适用补丁后，重新启动相应服务。

首次有限采集：

```sh
.venv/bin/python scripts/run_fullstack.py seed
```

`seed` 提交 OSV langchain、NIST、国内政策公开源任务，并单独导入明确标记的 demo 样本。计划采集默认关闭；这与查询时的按需外部工具不同。同步 worker 使用 `--all-scopes` 分别租用真实／demo 事件、索引和 ACK，本身不重新联网搜索。等待采集和索引完成后再评估问答。

## 历史摘录验证与当前动态验收

原 `scripts/verify_fullstack.py` 是 **extractive 基础联调验证器**，要求至少一个真实 CVE 的当前版本已 indexed；它在发送问答前拒绝 live 模式，不是平台模型的真实验收脚本。完整启动默认 live 后，运行该旧脚本须明确切换模式。若要复现纯本地基础链路，临时关闭外部工具：

```sh
RAG_EXTERNAL_TOOLS_ENABLED=false .venv/bin/python scripts/run_fullstack.py restart-agent --extractive
.venv/bin/python scripts/verify_fullstack.py
# 验证后恢复当前平台模型及动态查询：
RAG_EXTERNAL_TOOLS_ENABLED=true .venv/bin/python scripts/run_fullstack.py restart-agent --live
```

动态小型评测方法见 [dynamic_evaluation.md](dynamic_evaluation.md)。Mock 数据集仅验证控制流程与接口契约，不能报告成真实模型或真实网络成功；真实后端模式会请求真实情报及平台模型，可能消耗模型额度。

## 接入契约

| 方向 | 接口 | 认证／关键字段 |
|---|---|---|
| 前端 → 后端 | `GET /api/v1/auth/me` | 普通用户 Bearer；验证身份，不签发令牌 |
| 前端 → 后端 | `POST /api/v1/agent/query` | 普通用户 Bearer；`query/session_id/is_demo`；可选 `Idempotency-Key` |
| 后端 → Agent | `POST /v1/query` | Agent service token；V1 query、后端历史、预算 |
| Agent → 后端 | `POST /api/v1/internal/llm/complete` | tool token；固定 purpose、payload、尝试与时间／回答预算 |
| Agent → 后端 | `GET /api/v1/internal/llm/capabilities` | tool token；已配置状态及明确模型能力 |
| Agent → 后端 | `POST /api/v1/internal/intelligence/lookup` | write token；注册工具与类型化实体参数，无任意 URL |
| 同步 worker → 后端 | 知识事件 claim、当前 revision 读取 | tool／write token 按接口分工；真实／demo 分开处理 |
| 同步 worker → 后端 | ACK／索引确认 | write token；UUID/hash/index_id/operation；后端独立核验 |
| 前端 → 后端 | `GET /api/v1/agent/sessions/{id}` | 相同用户；消息正文、引用与执行记录 |

完整 V1 契约见 [backend_agent_v1.md](backend_agent_v1.md)。回答保留后端 UUID、原始 content_hash、来源定位，以及 `completed/partial/insufficient_evidence`。动态响应另含需求支持情况、检索轮次、停止原因和限制，不用假百分比代替证据支持。

后端转发 `X-RAG-Answer-Mode`、`X-RAG-Embedding-Mode`、`X-RAG-LLM-Calls`；未记录模式的历史消息明确标注未知。来源详情按引用的 content_hash 展示原版本；历史版本找不到时报错，不静默替换为当前版。用户认证故障、平台模型故障、检索故障分别展示。

## 实际验证与限制

2026-10-04 基础联调见 [fullstack_validation.md](fullstack_validation.md)，2026-10-10 原问答改进见 [qa_evidence_improvement.md](qa_evidence_improvement.md)，当时的默认摘录模式和资料数量属于历史快照。

2026-10-11 本机新链路已实际使用平台 DeepSeek、真实 E5 与官方情报源，覆盖问候、已有 CVE、工具审计、资料不足、包参数澄清、OSV 查询及编号补检索。运行记录在 `reports/local/fullstack/dynamic_live_validation.json` 与 `dynamic_live_captures.json`；记录包括来源版本与引用定位核对。NVD／CISA 的源故障或目录限制仍单独披露，不能把一次返回当作所有问题、所有来源都稳定可用。

错误 Key、限流、超时和千问切换主要通过 Mock 回归验证；千问真实调用、独立专家语义评审、复杂 CPE／版本适用性、资产匹配、生产账号登录、外网部署和高并发仍需独立验收。代码、接口和真实调用联通不等于这些内容已经完成。
