# 公开情报快照：团队导入说明

这是定期共享的数据快照，不是实时数据库。三台电脑都访问 `127.0.0.1` 时，连接的是各自的数据库；拉取代码不会自动同步数据。**不要交换 `.env`、API Key、服务令牌、PostgreSQL dump 或 Agent 的 SQLite 索引。**

## 本批数据与取得方式

- 仓库：`suyue2028-pixel/ICT`。
- 后端分支：`feature/backend-integration`，Draft PR #1；未合并时从此分支取得。
- 数据目录：`backend/datasets/public-intelligence/public-20261012-review1/`。
- 格式 `cit-public-intelligence` v1，最低迁移 `0010_public_snapshots`。
- 本批 13 个公开来源、2,401 份真实文档、2,527 个历史/当前版本、5,256 条字段证据；源端漏洞元数据 1,070 条、别名 3,288 条、文档关联 1,814 条。
- 6 条演示文档被排除，另有 4 条疑似敏感文档整条排除（含历史版本）；未改写其正文/哈希。被拦截 ID 只留导出端 ignored `output/` 报告，未上传内容。
- 生成时间与 SHA-256 以 manifest 为准。导出时采集仍在继续，本批是事务开始时的一致性视图。

已有团队仓库，在仓库根目录执行下面命令可查看远端数据，**不必合并前端或 Agent 分支**：

```powershell
git fetch origin feature/backend-integration
git log -1 origin/feature/backend-integration
git ls-tree -r --name-only origin/feature/backend-integration -- backend/datasets/public-intelligence
```

请在单独的干净工作区取得后端分支，避免覆盖自己未提交的代码：

```powershell
git worktree add --detach ..\ICT-public-snapshot origin/feature/backend-integration
cd ..\ICT-public-snapshot\backend
```

这样只创建本地工作区，不合并任何队友分支。也可从该分支下载 ZIP；校验依赖 manifest，SHA-256 不是发布者签名，只使用团队审核的提交。若已有兼容工具而只想取数据，可在 GitHub 该分支下载上述整个目录或使用 `git archive`/稀疏检出到一个新目录；不能漏掉任何 JSONL 分片。不要对有未提交改动的目录执行覆盖式 checkout。

后端 `.gitattributes` 对快照设置 `-text`，避免 Windows Git 将 LF/CRLF 自动转换后破坏 SHA-256。不要用编辑器重新保存 JSONL/manifest；若手动复制数据包，先运行 check。保留本批 ID 对应的原始文件字节。

## 配置与迁移

下面命令从**后端目录**执行。Windows 已有环境可使用 `D:\cit\.venv\Scripts\python.exe`；其他队友换成自己的 Python 3.12 虚拟环境。无需把我的绝对路径照抄到队友电脑。依赖以 `uv.lock` 为准，已有可用环境直接复用。

先检查 `README.md` 与 `.env.example`，准备**自己电脑的** `.env`，使用自己的数据库凭据与服务令牌。不要复制我的配置。新工作区可以创建到现有本地 `.env` 的仅本机引用或自行填写；它永远不应加入 Git。默认本机 PostgreSQL 是 5440 端口，容器内是服务名 postgres:5432。已有数据库按现有备份流程备份后执行增量迁移，不改写已执行迁移：

```powershell
$py = 'D:\cit\.venv\Scripts\python.exe' # 换成队友自己的虚拟环境
& $py -m alembic current
& $py -m alembic upgrade head
& $py -m alembic check
$packet = '.\datasets\public-intelligence\public-20261012-review1'
& $py scripts/public-snapshot.py check --input $packet
& $py scripts/public-snapshot.py import --input $packet --dry-run
& $py scripts/public-snapshot.py import --input $packet --apply
& $py scripts/public-snapshot.py verify --input $packet
```

如果在 `D:\cit\后端开发` 使用源码，数据包可指向 `D:\cit\ICT-upload-backend\backend\datasets\public-intelligence\public-20261012-review1`，不需要复制数据库。不要将本地数据库密码写到命令行参数中。

Docker 部署者沿用项目的 `docker compose build migrate` / `docker compose up -d` 完成镜像更新与迁移，再在后端宿主机运行上述脚本。没有 Windows Python 的 Linux 容器用户，可把数据包挂载到容器并在后端目录执行同一脚本；不会通过 API 上传可执行文件。**不要运行 `docker compose down -v`。**

运行镜像不包含 scripts/datasets；纯容器用户在后端目录用只读挂载执行（PowerShell 示例，三个操作分别执行）：

```powershell
$scriptsPath = (Resolve-Path .\scripts).Path
$packetPath = (Resolve-Path $packet).Path
docker compose run --rm --no-deps -v "${scriptsPath}:/app/scripts:ro" -v "${packetPath}:/snapshot:ro" api python scripts/public-snapshot.py import --input /snapshot --dry-run
docker compose run --rm --no-deps -v "${scriptsPath}:/app/scripts:ro" -v "${packetPath}:/snapshot:ro" api python scripts/public-snapshot.py import --input /snapshot --apply
docker compose run --rm --no-deps -v "${scriptsPath}:/app/scripts:ro" -v "${packetPath}:/snapshot:ro" api python scripts/public-snapshot.py verify --input /snapshot
```

导入为一次事务，任何校验/约束失败均回滚；修复外部条件后重新执行同一命令即可。dry-run 只查询，不通过“写入后回滚”模拟。新数据源默认关闭采集，既有数据源的开关、游标和调度不变。导入不会运行 POC 或调用付费模型。

## 怎样核对数量与版本

`verify` 的 `complete=true` 表示**本批**文档、原始版本、证据和当前关联已核对；不会删除额外数据。全库总数无需相等：队友可能有自己的新增资料、演示过滤条件不同或已有更近期版本。

- `matched.documents/revisions/evidence/associations` 对照 manifest。
- `current_matches_snapshot` 是当前版本相同的文档；`local_current_differs` 是本地保留了不同当前版本的文档，快照版本仍作为历史保存。
- `historical` 表示目标库的来源更新时间更晚；`conflict` 表示缺少可靠顺序或时间相同但内容不同，**不自动替换当前版本**。
- `uuid_remapped` 表示导出 UUID 已被无关记录占用，已分配新的本地 UUID；映射保存在 `snapshot_members`。所有引用以本机 document_id + 原始 content_hash 查询。
- `skipped_conflict`、缺失证据或不一致不可默认为成功；CLI 整批回滚并报错。先排查 tombstone/无关标识冲突，不能删除已有数据来“对齐”。
- 漏洞的本地 UUID/规范编号可以因合法别名合并而不同；当前关系按现有保守规则重建，文章多漏洞共现不会合并。历史别名及其原始关系仍在 revision/版本证据中；`linked_identifiers` 核对当前标识链接，不等同于源端漏洞行数。

来源发布时间/更新时间、源端首次发现、最近检查/内容变化及 revision/evidence 的时间保留原值。`snapshot_imports.imported_at` 单独记录本次导入时间，不能当作刚发布或真实采集成功。`indexed_at` 不从数据包复制。

## Agent 建索引

导入仅创建 pending KnowledgeEvent，**不表示 indexed**。现有 external 模式的 Agent 只认领 AI 相关当前版本（以及删除事件）；非 AI 资料仍可通过结构化/关键词 API 查询，pending 数不一定能清零。

在各自后端 `.env` 配置 `INDEX_PROVIDER=external`、本机 `AGENT_SERVICE_URL`；`AGENT_SERVICE_TOKEN`、`AGENT_TOOL_TOKEN`、`AGENT_WRITE_TOKEN` 使用自己生成的值。Agent 终端通过自己本地配置加载对应令牌，不在聊天、Git 或命令输出中显示。Agent 的 `.venv`/E5 缓存与索引继续使用本机现有环境；没有可用模型缓存或网络时，数据导入仍可完成，事件保持待处理，不能伪造索引。

从已有 Agent 工作区（例如 `D:\cit\ICT-team`）配置以下**非秘密**环境项；三个令牌按 Agent README 从本地配置加载：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'packages\rag_agent\src')
$env:RAG_BACKEND_URL = 'http://127.0.0.1:8010'
$env:RAG_BACKEND_DB_PATH = (Join-Path (Get-Location) 'data\backend-rag.db')
# RAG_SERVICE_TOKEN = 本机后端 AGENT_SERVICE_TOKEN
# RAG_BACKEND_TOOL_TOKEN = 本机后端 AGENT_TOOL_TOKEN
# RAG_BACKEND_WRITE_TOKEN = 本机后端 AGENT_WRITE_TOKEN
# 模型缓存/embedding 设置沿用本机 Agent README，不复制别人的 SQLite。
& .\.venv\Scripts\python.exe -m uvicorn examples.backend_api:app --host 127.0.0.1 --port 9001
```

另一个加载了相同本地配置的 Agent 终端先执行一小批，再启动持续同步：

```powershell
& .\.venv\Scripts\python.exe scripts/sync_backend.py --once --limit 5
& .\.venv\Scripts\python.exe scripts/sync_backend.py --limit 5 --poll-interval 5
```

若使用现有 `run_fullstack_windows.py start`，它已负责对应服务/同步配置，**不要再重复启动一个同步进程**。只有现有 Agent 取得租约、写入向量、验证可查询，后端又经 `/v1/indexes/verify` 核验 ACK 后才置 indexed。旧版本不能覆盖当前索引。

若未使用全栈启动器、Agent API 已运行，可从**后端目录**使用新增的便捷脚本，它从后端本地 `.env` 读取令牌到子进程内存，无需手工把令牌写在终端命令中：

```powershell
& $py scripts/sync-public-snapshot-agent.py --agent-root D:\cit\ICT-team --agent-python D:\cit\ICT-team\.venv\Scripts\python.exe --offline --once --limit 5
# 单批成功后去掉 --once，成为持续进程；路径换成队友的 Agent 工作区。
```

`--offline` 只使用本机已有 E5 缓存，没有缓存会明确失败，不会偷偷安装模型。同步进程使用 `data/backend-rag.db`，必须与已运行 Agent API 的 `RAG_BACKEND_DB_PATH` 一致；队友若自定义了索引路径，应沿用既有同步启动方式。同步本身不需要 DeepSeek，也不调用付费生成 API。

通过 `/api/v1/knowledge-events?status=pending`、`status=failed`、`status=indexed` 查看状态；列表是全库状态，本批精确状态使用 `public-snapshot.py verify` 的 `index_event_states`。失败重试沿用管理员鉴权的 `POST /api/v1/knowledge-events/{event_id}/retry`，不要把管理密钥交给前端。

## 下一批与维护

采集者从后端目录执行：

```powershell
& $py scripts/public-snapshot.py export --output output/public-snapshots/next-batch
& $py scripts/public-snapshot.py check --input output/public-snapshots/next-batch
```

输出只能位于被忽略的 `output/`。先查看 manifest 与旁边仅含 record ID 的 `*-rejected-records.json`，再审查并复制**数据包目录**到 `datasets/public-intelligence/<manifest.snapshot_id>/`，不要复制整个 output。工具默认 JSONL 分片约 8 MiB，单条上限 16 MiB、总包上限 256 MiB；GitHub 当前超过 50 MiB 警告、100 MiB 阻止，因此本方案不需要 LFS（[官方限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)）。

新快照用不同 ID（工具自动生成），禁止同 ID 改内容。下一份可以仍是完整快照；导入按稳定来源/外部文档 ID/版本哈希增量补齐，重复版本不重复写入、重复事件不重复制造。只有来源明确提供的更晚更新时间才替换当前版本，未知时间需人工审查。

在专用数据库运行测试，不在业务库运行测试夹具：

```powershell
& $py scripts/ci-check.py --new-db --check-contracts
```

真实索引验证和离线测试范围见 `public-snapshot-validation.md`。数据文件仅含公开来源的研究资料，保留原始作者/版权/许可信息；公开可访问不代表任意再分发许可，扩大传播前按来源条款审查。
