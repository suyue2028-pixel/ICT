# ICT · 智能体驱动的 AI 安全知识情报系统

多源安全情报监测、八类字段富化、Agent 工具编排与可追溯 RAG 问答。

`main` 是前端、后端与 Agent 的集成入口。三位成员继续在各自开发分支工作，阶段性修改通过 Pull Request 汇入 `main`。原有开发分支和提交历史保留。

## 先找到对应代码

- **前端：[`frontend/`](frontend/README.md)** — Vue 3、TypeScript、Vite；工作台、来源/富化分类、手机号登录、助手头像和可追溯问答。
- **后端：[`backend/`](backend/README.md)** — FastAPI、PostgreSQL、Redis、Celery；采集、富化、账户、会话、模型网关与证据校验。`backend/backend/` 是后端 Python 包，保留现有结构。
- **Agent：[`packages/rag_agent/`](packages/rag_agent/pyproject.toml)** — LangGraph 工作流、精确 CVE 与 BM25/E5 检索、证据补检、生成及输出核验。
- **共享接口与启动说明：[`docs/`](docs/GETTING_STARTED.md)、[`integration/`](integration/)、[`scripts/`](scripts/)**。
- **公开资料快照：[`backend/datasets/public-intelligence/`](backend/datasets/public-intelligence/)** — 定期导出的资料，不是采集机实时数据库。

可在 VS Code 中打开 [`ICT.code-workspace`](ICT.code-workspace)，按前端、后端、Agent 和共享集成资料浏览。

## 新成员从这里开始

完整步骤见 **[新机器接入与数据说明](docs/GETTING_STARTED.md)**。Agent 使用既有 Python 3.11 环境，后端要求 Python 3.12；另需符合前端要求的 Node.js，以及 Docker Compose 或后端文档规定的 PostgreSQL/Redis 环境。

1. 克隆本仓库并使用 `main`。
2. 按 [后端说明](backend/README.md) 初始化本地私有配置、数据库、迁移和采集服务。
3. 按 [Agent 接入说明](docs/backend_agent_v1.md) 启动 Agent API 与知识同步，确保后端和 Agent 服务令牌一致。
4. 在 `frontend/` 安装依赖并启动前端；开发代理默认连接 `http://127.0.0.1:8010`。
5. 登录后使用问答。真实模型服务由后端管理员配置，普通用户无需填写供应商 API Key。

前端开发地址通常为 `http://127.0.0.1:5173`，后端 API 文档通常为 `http://127.0.0.1:8010/docs`，Agent API 使用 `9001`。这些是本机地址，公开 GitHub 仓库不会自动部署公网产品网站。

## 数据为什么与采集机不同

GitHub 保存已提交的程序和公开快照。PostgreSQL 数据卷、Agent 的 SQLite 索引、用户会话和本地密钥独立保存在运行环境中，克隆代码不会同步这些内容。

已有 `public-20261012-review1` 快照记录 **13 个来源、2,401 篇文档、2,527 个修订、5,256 条证据和 1,070 条漏洞元数据**。使用前查看 [快照清单](backend/datasets/public-intelligence/public-20261012-review1/manifest.json) 和 [导入说明](backend/docs/public-intelligence-snapshots.md)，导入业务库后还需同步 Agent 索引。快照中没有用户、会话、凭据、模型调用或已完成索引；不将其数量当成当前采集量。

持续共享同一批资料时，各前端应连接同一套后端和数据库。第三方资料保留原来源版权及使用条件，本仓库未替第三方资料授予新的许可。

## 本次集成内容 · 2026-10-19

- 合入最新后端的八类富化、公开快照导入及手机号账户功能。
- 三方合并前端的手机号登录/富化展示与头像选择，保留两条分支各自更新。
- 以 `agent-dev` 的现行工作流为准；旧 `feature/agent-qa-relevance` 保留作历史参考，已被后续实现替代的代码不覆盖现行版本。保留其 Windows 条件时区依赖。
- 对齐现有长回答能力：后端回答预算默认 `8192` token、Agent 网关默认 `90` 秒、模型请求默认 `60` 秒，浏览器请求默认 `120` 秒；管理员显式环境配置优先。
- 修复模型调用次数元数据的上限差异，保留当前用户鉴权、来源版本及原文位置校验。

本次进行了源文件归档哈希核对、三方合并审查、敏感内容扫描和静态结构检查；**未重新运行功能测试或真实模型调用**。此前验证记录按日期保存在各模块文档中，不能当成本次集成的新测试结果。后端 CI 中固定版本的契约检查属于历史兼容基线。

## 团队分支约定

- `main`：三部分可共同检出的集成代码与总说明。
- `agent-dev`：Agent / RAG 开发。
- `feature/backend-integration`：后端开发。
- `codex/frontend-demo` 与 `feature/frontend-enrichment-evidence-20261015`：现有前端开发分支，本次已整合双方提交；后续修改按成员分支提交 PR。
- `feature/agent-qa-relevance`：旧环境修复与历史实验分支，保留查阅。

来源提交及处理方式见 [本次集成说明](docs/repository-integration/README.md) 和 [来源清单](docs/repository-integration/source-manifest.json)。`integration/*_patches/` 保留历史差异，新成员不要无差别重新应用，避免覆盖当前代码。

## 配置与能力边界

供应商模型密钥只配置在业务后端。`.env.example` 是模板；真实 `.env`、`.secrets/`、运行数据库、备份、日志、依赖缓存和模型权重不随 Git 上传。不同机器需要各自安装依赖并准备 E5 模型与索引。

DeepSeek 保留历史真实运行记录；其他 Provider、通用网页搜索和外部工具按实际配置说明，不把未配置或测试替身写成真实联网成功。资料缺失、服务异常和登录失效分别处理；公开 PoC 只展示元数据，不执行。
