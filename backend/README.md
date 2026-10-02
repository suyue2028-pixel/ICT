# AI 安全知识情报系统 · 后端开发

这是团队仓库的完整后端工程，工程内部的 `backend/` 为 Python 包，不要单独搬运 `main.py`。原开发与验收目录是 `D:\cit\后端开发`，使用 Windows Python 3.12 和 Docker Desktop；下方保留原机器的验证说明。本轮补齐 B1—B8 后端能力、Agent 数据工具和网关，并实际采集七类来源；队友的 Agent/RAG 服务尚未部署，独立 Embedding 尚未配置，二者均不会被模拟答案替代。

## 团队克隆后的运行方式

进入本 README 所在的后端工程目录，安装已核实的 Python 3.12、uv 和 Docker Desktop，然后运行：

```powershell
uv sync --locked
uv run python scripts/init-env.py
uv run python scripts/configure-agent-env.py
docker compose up -d --build --wait
docker compose ps --all
```

uv 在当前工程建立 `.venv`，项目依赖和开发依赖采用 `uv.lock`。Docker 镜像使用另附的哈希锁定 `requirements.txt`。初始化脚本只在不存在 `.env` 时生成随机本地凭据，并选择空闲端口；已有 `.env` 会保留，不要直接把模板占位密码用于运行，也不要复制其他成员的 `.env`。

默认 Compose 项目和镜像为 `ict-backend`，可通过本地 `COMPOSE_PROJECT_NAME` 覆盖。它与原机器的 `cit-backend-development` 项目及数据卷分开；请先确认自己的项目名与数据库配置，原开发数据库不会自动迁移到新副本。此提交没有数据库数据、备份或真实模型密钥。

初始化后查看脚本输出的 API 端口，例如默认空闲时文档地址为 `http://127.0.0.1:8010/docs`。停止用 `docker compose stop`，重新启动用 `docker compose start --wait`，日志用 `docker compose logs --tail 100 api worker beat migrate`。不要使用删除数据卷的命令。

Windows 调试打开当前目录的 `后端开发.code-workspace`，F5 采用项目内 `.venv`、启动 API 8011；数据库与后台任务继续使用容器。在 macOS/WSL/Linux 中选择当前工程的 `.venv/bin/python`，用 `uv run uvicorn backend.main:app --host 127.0.0.1 --port 8011` 启动 API。8011 如被占用，修改调试端口或命令，不结束其他进程。

测试和迁移检查在数据库已启动后执行 `uv run pytest -q -p no:cacheprovider`、`uv run alembic check`。真实采集和问答的验证脚本会增加记录，模型调用可能计费；具体说明和边界见下文。Agent/前端同学优先阅读 [联调契约](docs/AI_INTEGRATION.md)，凭据通过服务端本地配置，不能进入前端代码。

## 原开发机器的运行与验证说明

在 VS Code 打开 `D:\cit\后端开发\后端开发.code-workspace`，启动 Docker Desktop，在 PowerShell 执行：

```powershell
cd 'D:\cit\后端开发'
docker compose up -d --build --wait
docker compose ps --all
docker compose logs --tail 100 api worker beat migrate
docker compose stop
docker compose start --wait
```

若当前终端找不到 `docker`，将命令前的 `docker` 替换为 `& 'C:\Users\lx\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'`。迁移服务自动执行增量迁移和来源初始化，完成后退出 0 属正常现象。运行中的服务为 api/postgres/redis/worker/唯一 beat。

- API 文档：<http://127.0.0.1:8010/docs>
- 就绪：<http://127.0.0.1:8010/health/ready>；存活：<http://127.0.0.1:8010/health/live>
- 原开发机器继续复用 `D:\cit\.venv`；团队副本的 F5 配置使用项目内 `.venv`。调试文档为 <http://127.0.0.1:8011/docs>。入口 `backend/main.py`，通过 uvicorn 启动。PostgreSQL、Redis、worker、beat 仍使用 Docker。
- 本机 PostgreSQL 5440、Redis 6380、容器 API 8010 只绑定 `127.0.0.1`；容器用 postgres/redis 服务名连接。

新机器用 `scripts/init-env.py` 初始化随机本地凭据，再运行 `scripts/configure-agent-env.py` 增补独立内部凭据；现有 `.env` 不覆盖。不要提交 `.env`、虚拟环境、缓存或 `output/`，也不要删除数据库卷。

## 实际具备的能力

API 返回 202 和任务 ID，任务/outbox 一起提交；beat 补发 Redis 消息，Linux Celery worker 采集、规则筛选、保守按显式标识关联、保存原文/版本/证据/知识事件，并与来源游标一起提交。失败有稳定错误码、有限重试、租约恢复；重复导入幂等。规则分类不是已经测得的模型准确率。

| 类别 | 实际适配器 | 内容与边界 |
|---|---|---|
| 漏洞库 | OSV、NVD | 包/版本、CVE/GHSA 等别名、CWE、CVSS、公开补丁/分析/POC 链接 |
| 厂商公告 | Cisco PSIRT、NVIDIA | Cisco 官方 RSS；NVIDIA AI 产品官方目录摘要，未抓取被阻止的公告正文 |
| 安全社区 | SANS ISC、Hugging Face | SANS RSS；HF 公开搜索的提示注入讨论摘要。泛 AI 帖子不再判相关 |
| 安全博客 | Trail of Bits | RSS 技术分析，按载荷标明全文或摘要 |
| 学术论文 | arXiv、Crossref | 摘要/DOI/作者/元数据，未下载论文全文；仅元数据不能凭标题判为 AI 相关 |
| 技术标准/规范 | NIST AI RMF | 官方 HTML 框架章节，属于自愿框架，未宣称 ISO/强制标准全覆盖 |
| 政策法规 | GOV.UK、中国政府网 | 政策/指导与《生成式人工智能服务管理暂行办法》法规正文，法律性质分别标记 |

来源类别、文档类型、规则风险类别分别保存。文档支持 vulnerability/advisory/discussion/article/paper/standard/policy/regulation；正文完整性为 full_text/summary/metadata_only/partial/unknown。来源信息缺失为 null。七类不是七个 URL，规则相关数量也不代表经过人工确认。

本机已启用真实来源增量调度，多数每小时，arXiv 每天以适应其 API 缓存；续页任务可能每分钟执行。首次回填独立统计。周期不能证明发布到采集 ≤6h，当前有超过六小时的可观测样本，尚未达到持续 SLA。固定法规/规范目录不代表发现所有新法规或标准。

五种基本富化维度 affected/cwe/cvss/remediation/analysis 已在真实数据中有源字段证据；NVD 还实际取得公开 POC 链接元数据。版本范围算一个维度，POC 不执行。模型回写保存 extraction_method/review_state 和引用，不因引用有效就宣称推断正确。关联论文的推理留给 Agent，未冒充已完成。

## 权限与接口

| 身份 | 服务端配置/用户凭据 | 用途 |
|---|---|---|
| 管理员 | `.env` ADMIN_API_KEY，HTTP `X-API-Key` | 来源/任务/演示管理、用户令牌签发、旧 `/qa/ask` |
| Agent 只读 Tool | AGENT_TOOL_TOKEN，HTTP `X-Agent-Token` | `/api/v1/tools/*`；不能索引确认或富化回写 |
| 索引/富化消费者 | AGENT_WRITE_TOKEN，HTTP `X-Agent-Token` | `/internal/knowledge-events/*`、`/internal/enrichments` |
| 普通用户 | 管理员签发的短期、可撤销 Bearer token | `/agent/query` 和自己的会话/调用记录；前端只持此令牌 |

管理员用 `POST /api/v1/auth/tokens` 签发 `{"owner_id":"developer1","expires_in_hours":24}`，令牌仅一次显示，库内只保存 hash；`DELETE /auth/tokens/{id}` 撤销。不要将管理员、Agent 或模型密钥交给前端。所有凭据只在本地或服务器私密配置，不发送到聊天。

| 前缀 `/api/v1` | 能力 |
|---|---|
| `/sources`、`/sources/{id}/collect`、`/tasks`、`/tasks/{id}/retry` | 来源管理、202 异步采集、步骤/失败重试 |
| `/intelligence`、`/documents`、`/documents/{id}/enrichment`、`/relations/{id}` | 分页情报、原文/版本、字段状态、显式关系 |
| `DELETE /documents/{id}` | 202 逻辑失效，保留原文/历史/证据，产生 delete 事件 |
| `/tools/cves`、`/tools/cves/{CVE}`、`/tools/nvd`、`/tools/vendor-advisories`、`/tools/documents` | 有 JSON schema 的 Agent 查询；NVD 只返回 NVD 记录 |
| `/knowledge-events`、`/internal/knowledge-events/claim`、`/internal/knowledge-events/{id}/ack` | 索引状态、外部独占租约、可核验确认 |
| `/knowledge-events/{id}/retry`、`/knowledge-events/{id}/replay` | 失败重试、当前版本重放；不能重放旧版本覆盖新版本 |
| `/internal/enrichments` | 按当前版本、真实引用回写结构化富化 |
| `/agent/query`、`/agent/sessions`、`/agent/invocations/{id}`、`/agent/health` | 队友 Agent 适配入口、归属会话、调用轨迹、管理员健康/版本检查 |
| `/qa/search`、`/qa/semantic-search`、`/qa/ask`、`/qa/conversations` | 保留关键词/内置语义/DeepSeek/旧会话能力 |
| `/ops/metrics`、`/stats` | 脱敏监控、真实覆盖、延迟、字段证据、错误/积压 |

`/demo/import` 是三份明确虚构的离线验证资料；必须显式 `is_demo=true`，不会计入真实来源成绩。统一错误为 `error.code/error.message/request_id`，分页最大 100。

## DeepSeek、索引与 Agent 的切换

现有 `DEEPSEEK_API_KEY` 保留，旧 `/qa/ask` 默认仍用 DeepSeek；可传 `conversation_id`，最近 8 条历史、单会话最多 100 轮。实际两轮已验证，证据不足明确返回 insufficient_evidence。当前真实检索方式是 keyword，不声称语义检索已运行。

内置索引：`INDEX_PROVIDER=internal`。现有 PostgreSQL 16 镜像无 pgvector，使用数据库浮点数组与精确余弦检索，保留旧卷。需另配本地 EMBEDDING_API_KEY、EMBEDDING_BASE_URL（官方云端业务空间/地域地址），默认 text-embedding-v4，不能使用 DeepSeek Key 代替。配置后任务会处理 pending 文档，云调用可能计费。只有真实向量写入可查询才为 indexed，缺配置明确 503。未对无向量的空库编造性能数据。

外部队友模式：服务可用后设置 `AGENT_SERVICE_URL`、按需 `AGENT_SERVICE_TOKEN` 和 `INDEX_PROVIDER=external`。容器访问 Windows 服务可用 `http://host.docker.internal:端口`；工具 URL 由队友服务的网络位置决定。两个索引消费者不会同时派发同一事件。`/agent/query` 使用外部 Agent；当前服务未配置，返回 503。旧 `/qa/ask` 切换旧外部问答协议时设 `QA_PROVIDER=external` 并设置 `AI_SERVICE_URL`，无需删除 DeepSeek Key。这是两个明确的兼容接口，详见联调文档。

修改 `.env` 后运行 `docker compose up -d --force-recreate --wait api worker beat`，确保所有容器收到配置；在索引处理中不要切换 provider，先等待或处理租约。

## 测试与评分证据

```powershell
& 'D:\cit\.venv\Scripts\python.exe' -m pytest -q
& 'D:\cit\.venv\Scripts\python.exe' -m alembic check
& 'D:\cit\.venv\Scripts\python.exe' scripts\verify.py --restart-check
& 'D:\cit\.venv\Scripts\python.exe' scripts\verify_live.py
& 'D:\cit\.venv\Scripts\python.exe' scripts\verify-runtime.py
& 'D:\cit\.venv\Scripts\python.exe' scripts\export-evaluation.py
& 'D:\cit\.venv\Scripts\python.exe' scripts\evaluate-reviewed.py output\evaluation-review.json
```

运行验证会创建专用离线资料、真实采集记录或少量问答历史，保留其他数据；重启检查会短暂中断本项目。verify-runtime 发起两次真实 DeepSeek 请求，不打印令牌或生成内容。导出的 review 文件保留 null 标注，需独立审核后才能计分，模拟测试、字段数量与一次问答不等于准确率/召回率达标。

- [后端与 Agent/RAG 契约](docs/AI_INTEGRATION.md)
- [职责/缺口/验收矩阵](docs/BACKEND_UPGRADE_STATUS.md)
- [来源与质量边界](docs/SOURCE_COVERAGE.md)
- [评分评测流程](docs/EVALUATION.md)
- [实际验证记录](docs/VERIFICATION.md)
- [架构与可靠性](docs/ARCHITECTURE.md)

真实 Agent、云端语义索引、跨文档推理及标注后的准确率/召回率仍需相应服务/审核；本次不制作前端、不处理 GitHub、不执行 POC 或资产扫描。
