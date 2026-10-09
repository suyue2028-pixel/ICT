# AI 安全知识情报系统 · 后端开发

本次同步包含富化质量、14适配器采集覆盖/时效、版本化自动关联、Agent回答证据闭环、CI与隔离恢复和演示材料。保留团队可移植配置，原开发机器验收事实与团队新部署配置分开说明。CI位于仓库根`.github/workflows/backend-ci.yml`。

本分支同步到已有后端 PR，CI 在 push/PR 自动触发，结果以 PR Checks 为准。交叉契约测试只读检出 Agent PR 的固定提交 `8bceeb9c1ac653b9a107487412726aa4806fb6dc`，不把 Agent 代码复制到后端或改变队友分支。下文“远端CI尚未触发”等句子属于原机器开发阶段验收快照。团队机器做隔离容器演练请使用 `uv run python scripts/verify-fault-recovery.py --image ict-backend:local`；默认镜像名的脚本为原机器配置。

本次上传副本复验：127项测试通过、0跳过；新验证数据库迁移与模型一致，接口契约无漂移，Redis PING及API就绪通过。测试显式覆盖内部与外部索引的失效确认规则；未调用真实模型，未复制本机`.env`。一条Starlette依赖弃用警告仍待上游兼容升级。远端CI结果以当前PR提交的Checks为准。

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

测试请在数据库已启动后执行 `uv run python scripts/ci-check.py --new-db --check-contracts`，该命令只在新验证数据库运行；迁移状态可用 `uv run alembic check` 查看。真实采集和问答的验证脚本会增加记录，模型调用可能计费；具体说明和边界见下文。Agent/前端同学优先阅读 [联调契约](docs/AI_INTEGRATION.md)，凭据通过服务端本地配置，不能进入前端代码。

## 原开发机器的运行与验证说明

2026-10-09 已补“自动关联→Agent回答→原文证据”闭环：版本化关联依据、回答证据追溯和引用位置核验；新增本地/CI统一检查、独立容器故障演练与演示脚本。使用方法及真实验收边界见 [闭环与恢复](docs/TRACEABILITY_CI_RECOVERY.md)、[五分钟演示](docs/DEMO_RUNBOOK.md)。现有前端与Agent保持兼容；远端GitHub Actions尚未触发。

2026-10-09 真实覆盖与时效补强：NVD近期优先/历史补采、默认12关键词、OSV默认10包、NVIDIA完整目录分页；增加NIST草案/GOV.UK政策发现，共14个真实适配器、仍7类。新增管理员 `/ops/coverage` 和现有beat中的覆盖/实测延迟采样。范围、运行方法和验收边界见 [真实覆盖与时效V2](docs/COVERAGE_FRESHNESS_V2.md)。

2026-10-09 富化质量监测已补齐：六维覆盖、原文/结构问题、版本固定审核批次、人工标签与分维度precision/recall；未审核不会显示质量分数。使用方法见 [富化质量审核](docs/ENRICHMENT_QUALITY.md)。独立验证数据库完整迁移与102项回归通过；最终部署结果记录另见验证文档。

2026-10-09 已完成进一步改进：36 问题可复现评测、英文检索主题约束、跨用户权限与异常契约回归、来源版本范围结构化查询、持久化 48 小时观察、隔离备份恢复与有上限性能验证。已恢复本机三端，并核对运行容器源码哈希。详见 [本轮验收](docs/IMPROVEMENTS_20261009.md) 和 [实际运行、观察与恢复](docs/OPERATIONS_20261009.md)。观察尚未结束，已记录缺口；评测标注尚未独立人工审核。

2026-10-08 追溯问答修复已在本机部署：检索与证据筛选、多轮追问、生成模式及调用次数保存。新增会话可在 http://127.0.0.1:5173/qa 验证；旧历史保持原样。见 [问答修复与接口说明](docs/QA_REPAIR.md)。

2026-10-08 已部署最近 48 小时筛选、来源状态、失败退避、恢复补采和当前版本索引时间。容器已统一从本目录构建，保留此前前端/Agent 兼容改动；外部 Agent 与索引同步已恢复，DeepSeek 保留。见 [时效性运行说明与验收](docs/FRESHNESS.md)。下方“外部服务未配置”等描述是早期阶段，当前本机连接以此更新为准。

现有项目继续开发于 `D:\cit\后端开发`，复用 Windows Python 3.12 虚拟环境 `D:\cit\.venv` 和 Docker Desktop。原数据、历史版本、DeepSeek 与数据库卷保留。B1—B8 后端能力、Agent 数据工具和网关已实现；本机外部 Agent/RAG 已部署，使用真实缓存 E5 语义索引。独立云端 Embedding 未配置，对应内部索引模式仍需另外配置。

## 运行与调试

在 VS Code 打开 `D:\cit\后端开发\后端开发.code-workspace`，启动 Docker Desktop。本机三端请优先使用 [运行说明中的 Windows 启动脚本](docs/OPERATIONS_20261009.md)，它保留 Agent overlay。以下为独立后端的 Compose 命令：

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
- VS Code F5 选“后端开发：FastAPI（本机 8011）”，文档为 <http://127.0.0.1:8011/docs>。入口 `backend/main.py`，通过 uvicorn 启动。PostgreSQL、Redis、worker、beat 仍使用 Docker。
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

现有 `DEEPSEEK_API_KEY` 保留，旧 `/qa/ask` 默认仍用 DeepSeek；可传 `conversation_id`，最近 8 条历史、单会话最多 100 轮。证据不足明确返回 insufficient_evidence。旧接口检索方式为 keyword；前端追溯问答实际走 `/agent/query`，使用外部 Agent 的真实 E5 混合检索，二者不能混用验收结果。

内置索引：`INDEX_PROVIDER=internal`。现有 PostgreSQL 16 镜像无 pgvector，使用数据库浮点数组与精确余弦检索，保留旧卷。需另配本地 EMBEDDING_API_KEY、EMBEDDING_BASE_URL（官方云端业务空间/地域地址），默认 text-embedding-v4，不能使用 DeepSeek Key 代替。配置后任务会处理 pending 文档，云调用可能计费。只有真实向量写入可查询才为 indexed，缺配置明确 503。未对无向量的空库编造性能数据。

外部队友模式设置 `AGENT_SERVICE_URL`、按需 `AGENT_SERVICE_TOKEN` 和 `INDEX_PROVIDER=external`。本机启动脚本已通过 overlay 配置容器访问 Windows Agent 9001（host.docker.internal）；工具 URL 由队友服务的网络位置决定。两个索引消费者不会同时派发同一事件。`/agent/query` 当前真实可用；在其他未配置部署上明确返回503。旧 `/qa/ask` 切换旧外部问答协议时设 `QA_PROVIDER=external` 并设置 `AI_SERVICE_URL`，无需删除 DeepSeek Key。这是两个明确的兼容接口，详见联调文档。

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

本机真实 Agent 和 E5 索引已联调；独立云端语义索引未配置。跨文档推理质量、独立人工标注后的准确率/召回率与持续运行 SLA 仍待验收。未制作前端、未执行 POC 或资产扫描。

富化质量监测在团队副本中使用 `uv run python scripts/enrichment-quality.py summary`；审核和评分见[富化质量说明](docs/ENRICHMENT_QUALITY.md)。普通团队部署由迁移服务增加0008质量表和0009关联依据表。没有独立人工标签时质量分数保持未知。

本机恢复、观察和部署脚本中含原机器路径与容器名的工具只供原机器复验。团队新克隆使用上方通用启动方式。跨仓库契约测试可用 `ICT_AGENT_CONTRACT_FILE` 指向公开的Agent `integration/contracts.py`；CI已固定检出该契约，本次上传复验未跳过此测试。
