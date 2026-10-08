# ICT RAG Agent

2026-10-08 Windows 追溯问答已修复并部署：相关性筛选、结论支持检查、多轮追问及冷启动预热；Agent 86 项、后端 70 项回归通过，七组真实 DeepSeek/无证据请求和 15 项运行检查已完成。详细结果、限制和本地修改交接见 [问答修复记录](docs/qa_repair_windows.md)。下方早期 extractive/未实调说明保留原阶段记录。

Windows 本机三模块入口：**http://127.0.0.1:5173**。现有 Docker 数据库、后端与 Agent/DeepSeek 已实际联调；启动、停止、验证及回退见 [Windows 运行说明](docs/fullstack_windows.md)。本机适配代码尚未推送，以下 macOS 记录保留其原验收范围。

已实现可运行的 AI 安全情报 RAG 模块，代码保存在本仓库 `packages/rag_agent`。团队前端和后端通过服务契约接入。Agent 使用独立分支 `agent-dev`。

## 团队分支

| 负责模块 | 分支 | 主要目录 |
|---|---|---|
| 前端 | `codex/frontend-demo` | `frontend/` |
| 后端 | `feature/backend-integration` | `backend/` |
| Agent / RAG | `agent-dev` | `packages/rag_agent/`，以及配套 docs/scripts/examples/reports |

三人分别在自己的分支提交和推送。核心Python契约见 `docs/contracts.md`；与当前后端真实分支的HTTP接入见 [docs/backend_agent_v1.md](docs/backend_agent_v1.md)。

## 10月4日全栈联调

前端、PostgreSQL/Redis/Celery 业务后端、真实 E5 Agent 已在本机跑通。浏览器访问 **http://127.0.0.1:5173**；资料由后端采集，Agent 消费知识事件、索引/ACK并检索，前端通过后端网关问答与追问。

```sh
.venv/bin/python scripts/run_fullstack.py start --detach
.venv/bin/python scripts/run_fullstack.py status
.venv/bin/python scripts/verify_fullstack.py
.venv/bin/python scripts/run_fullstack.py stop
```

已有服务运行时直接打开页面。当前47份真实资料/47个indexed事件，另有明确隔离的虚构样本。默认 **extractive 来源摘录，远程模型调用0**；Flash接口配置可复用团队密钥，真实生成尚待验证。

运行目录、首次安装、密钥、模块接口与部署边界见 [全栈运行与交接](docs/fullstack_local.md)，结果见 [本轮验证](docs/fullstack_validation.md)。下列19篇资料及旧验证数字保留为此前独立Agent阶段记录，当前整链状态以上述报告为准。

## 后端 HTTP V1 接入

新增带服务认证的 `/v1/health`、`/v1/query`、`/v1/indexes/verify`，保留后端UUID/hash与原始来源证据；同步worker消费后端带租约的知识事件，维护真实/demo隔离、精确版本映射与删除失效。默认使用独立 `data/backend-rag.db`，与原有19篇演示资料分开。

从仓库根目录设置 `RAG_SERVICE_TOKEN`（与后端 `AGENT_SERVICE_TOKEN` 一致）、`PYTHONPATH/HF_HOME` 后运行：

```sh
.venv/bin/python -m uvicorn examples.backend_api:app --host 127.0.0.1 --port 9001
```

在另一个终端设置后端地址、读写令牌和相同数据库，再启动同步：

```sh
.venv/bin/python scripts/sync_backend.py --once
```

完整环境变量、后端配置和同步流程见 [V1接入文档](docs/backend_agent_v1.md)。本机PostgreSQL/Redis/迁移与三模块整链现已验证，见全栈报告。独立Agent用例仍不代替业务整链验收。大模型可使用本人或团队的API账号，支持本地密钥文件；见 [大模型配置](docs/llm_configuration.md)。

## 当前真实完成状态

- Python 3.11、LangGraph、Pydantic、SQLite 版本化存储；原文、证据、断言和 float32 向量原子提交。
- 精确编号检索、jieba/BM25 + E5 余弦检索与 RRF、硬过滤、已记录实体关系的一跳扩展。
- 证据不足/部分回答/冲突/指代澄清/最多一次检索改写；最多 3 次模型 HTTP 请求、8 次工具调用和 30 秒总超时。
- 证据详情、带来源的 CVSS 和原始 CPE 条件；没有来源的修复版本保持 unknown。规则关联不等于攻击链确认。
- 19 篇真实资料、4 种文档类型、4 类采样渠道。来源覆盖 NVD、OWASP、arXiv、欧盟委员会；OWASP 内容为行业指导，不是正式规范性标准。固定源采样不是持续监测。
- 本机 CPU 已运行固定版本 `intfloat/multilingual-e5-small`；当前回答为 `extractive`，未接入远程生成模型。远程 DeepSeek JSON 请求适配已完成并由 HTTP 测试替身验证，缺密钥未实调。

人工引用支持率、中文生成质量、跨文档推理正确率均待评测，不能把下列演示指标解释为比赛达标。

本次验证：54 项合成回归通过，25 个真实资料用例通过自动检查，13 项本机 HTTP 检查通过。验证汇总、硬件、指标分母和限制见 [docs/validation.md](docs/validation.md)，结构化结果见 `reports/validation_summary.json`。

10月4日补充验证：原54项回归重新通过，新后端V1适配27项实际本机HTTP检查通过（合成后端身份/资料、真实E5、远程LLM调用0）。本轮状态见 [Agent V1验证记录](docs/agent_v1_validation.md)。这不代表真实PostgreSQL/Redis后端整链或真实大模型生成已经验收。

## 已有本机环境直接运行

从 `/Users/gshy/Desktop/ICT-RAG` 执行。该目录已经创建 Python 3.11.16 虚拟环境、下载模型并导入 `data/ict.db`。

```bash
cd /Users/gshy/Desktop/ICT-RAG
source .venv/bin/activate
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"
export HF_HUB_OFFLINE=1

python -m rag_agent.cli health
python -m rag_agent.cli query 'CVE-2024-37032 的评分和受影响范围'
python -m rag_agent.cli query '什么是提示注入风险？' --filters '{"doc_types":["standard"]}'
python -m rag_agent.cli query --request-file examples/queries/multi_turn.json
python -m rag_agent.cli query --request-file examples/queries/compare.json
python -m uvicorn examples.demo_api:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/docs`。演示适配器提供 `GET /health`、`POST /demo/query`、`GET /demo/evidence/{evidence_id}`。

## 新机器安装与重现

需要 Python 3.11。当前验证机器为 macOS ARM64/CPU，其他平台需要自行核对 PyTorch wheel。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation packages/rag_agent
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"

python scripts/download_model.py
python scripts/fetch_demo_sources.py
export HF_HUB_OFFLINE=1
python -m rag_agent.cli import data/demo/documents.jsonl
```

`requirements.lock` 锁定本次实际安装的直接和间接依赖。首次模型下载显式执行；正常导入和查询只读取本地模型缓存，缓存缺失时返回错误，不偷偷下载模型。中文问题的 token 数、标题和 query:/passage: 前缀均受 512 tokens 限制，不默默截断。

采样记录在 `data/demo/manifest.json`，包括原始 URL、最终 URL、时间、SHA-256、重试次数、失败记录。下载文件和 `documents.jsonl` 被 Git 忽略。重现当前本机资料解析可执行 `python scripts/fetch_demo_sources.py --reparse`，它校验缓存 hash 后解析；重新抓取会得到来源的新快照，验收报告的 hash 应重新审查。

## 验证命令

```bash
PYTHONPATH=packages/rag_agent/src python -m pytest packages/rag_agent/tests -q
python -m rag_agent.evaluation.run --cases data/evaluation/acceptance.json --output reports/evaluation.json
```

离线合成边界测试在 `tests/fixtures`，明确标记 synthetic；没有混进真实资料。验收用例为 25 个预置来源片段与字段参考，冻结后执行，没有按同一验收结果调答案；**本次参考由实现方定义，尚未有人类评审签字**。人工工作表为 `reports/human_review.json`。评测口径、rubric 和限制见 `docs/evaluation.md`。

真实重复导入结果为 19 unchanged、0 failed，generation 不变；安装后的 CLI 多轮/比较示例已执行，记录分别在 `reports/idempotence.json` 和 `reports/demo_examples.json`。

## 远程生成配置

配置字段见 `.env.example`；包不会自动读取 `.env`，请在终端或后端进程环境中设置变量。设置 `RAG_LLM_MODE=live`、`RAG_LLM_KEY` 或 `RAG_LLM_KEY_FILE`、`RAG_LLM_MODEL` 和可选 `RAG_LLM_BASE_URL`，才会调用远程模型。默认 provider 为 DeepSeek Chat Completions JSON 接口，model 可配置。`live` 缺密钥会立即报配置错误；`mock` 为显式测试替身，`extractive` 为有来源的摘录。

模型输出只提供 claims；facts 从存储断言渲染，引用 URL 从证据存储获取。非法引用、虚构结构字段和缺乏依据的编号/版本数字会触发有来源摘录回退。引用存在仍不意味着自然语言结论已被语义证明。

## 团队交接

核心Python接口为 `ingest`、`enrich`、`query`、`get_evidence`；当前业务后端通过HTTP V1接入，负责公开 `/api/v1/agent/query`、会话、采集任务和用户权限。契约和接入样例见 `docs/contracts.md` 与 `docs/backend_agent_v1.md`，机器可读 JSON Schema 位于 `docs/schemas/`。参考核查见 `docs/reference_review.md`；下一阶段分工与限制见 `docs/handoff.md`。

密钥、模型权重、数据库、缓存和大型原文下载均被 `.gitignore` 排除。框架不会执行 POC、互联网资产扫描或任意 SQL/网络工具；后续资产匹配仅接入团队提供的授权资产清单。
