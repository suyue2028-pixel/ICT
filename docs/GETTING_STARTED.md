# 新机器接入与数据说明

本说明针对 `main` 中同时存在 `frontend/`、`backend/`、`packages/rag_agent/` 的集成版本。环境准备与运行结果分开：本次只整理源码，未在新机器重新执行端到端验收。

## 1. 依赖与执行目录

- 前端：Node.js `^22.18.0 || >=24.12.0`，命令在 `frontend/` 执行。
- 后端：Python `>=3.12,<3.13`，依赖以 `backend/uv.lock` 为准；命令在 `backend/` 执行。Docker 镜像已固定 Python 3.12。
- Agent：Python 3.11 为既有验证环境，包要求 `>=3.11,<3.13`；根 `requirements.lock` 包含 Agent 与开发运行依赖，命令从仓库根目录执行。
- PostgreSQL、Redis 和 Celery 可按后端 Compose 启动。不同模块使用独立 Python 环境，避免将两套锁文件混装。

## 2. 准备后端

在 `backend/` 执行：

```sh
uv sync --locked
uv run python scripts/init-env.py
uv run python scripts/configure-agent-env.py
```

初始化脚本生成本机数据库与管理员凭据；另一个脚本补充只读和写入 Agent 服务令牌。随后按 [后端 README](../backend/README.md) 设置本地 `.env`，再启动：

```sh
docker compose up -d --build --wait
```

数据库迁移随 `migrate` 服务执行。API 端口以初始化输出为准，默认通常是 `8010`。已有本地 `.env` 会被保留，显式旧配置也会保留。

Agent 接入需要配置 `INDEX_PROVIDER=external`、`AGENT_SERVICE_URL`、`AGENT_SERVICE_TOKEN`、`AGENT_TOOL_TOKEN` 和 `AGENT_WRITE_TOKEN`。后端服务令牌与 Agent 对应变量必须一致；令牌只在自己的运行环境填写，不从 GitHub 获取共享秘密。

若后端在 Docker、Agent 在宿主机，后端配置的 Agent 地址必须能从容器访问。Docker Desktop 常使用 `http://host.docker.internal:9001`；其他系统按自己的容器网络配置。Agent 访问后端的地址通常仍是宿主机 `http://127.0.0.1:8010`。不要把容器内的 `127.0.0.1` 当成另一进程所在宿主机。

真实生成只在后端配置模型供应商及 API Key。可使用 `LLM_PROVIDER`、`LLM_MODEL` 与 `LLM_API_KEY`，或者项目支持的供应商变量；详见 [模型说明](llm_configuration.md)。普通用户在前端注册/登录后使用平台模型。手机号账户当前不包含短信所有权验证。

## 3. 安装与启动 Agent

在仓库根目录创建独立环境并安装既有锁定依赖：

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation packages/rag_agent
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"
python scripts/download_model.py
```

Windows 使用本机 Python 创建环境和 `.venv\Scripts\Activate.ps1`，按 PowerShell 方式设置环境变量；Agent 的条件 `tzdata` 依赖补充 Windows 系统可能缺少的 IANA 时区数据。旧 `feature/agent-qa-relevance` 的机器专用 Windows 启动脚本不作为本版默认入口。

按 [Agent V1 接入契约](backend_agent_v1.md) 在 Agent 进程设置：

- `RAG_SERVICE_TOKEN`：等于后端 `AGENT_SERVICE_TOKEN`。
- `RAG_BACKEND_TOOL_TOKEN` / `RAG_BACKEND_WRITE_TOKEN`：对应后端读写令牌。
- `RAG_BACKEND_URL` 与 `RAG_LLM_GATEWAY_URL`：指向后端可访问地址。
- `RAG_BACKEND_DB_PATH`：例如本机 `data/backend-rag.db`。
- `RAG_LLM_TRANSPORT=backend`；使用平台真实生成时设置 `RAG_LLM_MODE=live`。

不要将供应商 Key 注入 Agent 进程。配置就绪后从仓库根目录启动：

```sh
python -m uvicorn examples.backend_api:app --host 127.0.0.1 --port 9001
```

新终端使用同一 Agent 环境和读写配置执行知识同步：

```sh
python scripts/sync_backend.py --once
```

一次同步只处理一个批次；持续同步见脚本和 V1 文档。使用 `extractive` 模式只返回来源摘录，不能当成模型真实生成成功。

## 4. 启动前端

在 `frontend/` 执行：

```sh
npm ci
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 默认将 `/api` 转发至 `http://127.0.0.1:8010`，可用 `ICT_BACKEND_ORIGIN` 调整。前端默认使用 `/api/v1`；生产需要同域 API 反向代理和 SPA 路由回退。前端普通请求默认等待 120 秒，生产反代需配套，平台密钥不放入 `VITE_` 变量。

手机号登录需要后端迁移 `0012`，八类富化需要 `0011` 及对应 worker。头像选择、手机号登录和富化展示均已保留，详见 [前端 README](../frontend/README.md)。

## 5. 导入公开快照并同步检索

本仓库包含 `backend/datasets/public-intelligence/public-20261012-review1/`。这是既有审核导出的定期包，不包含用户/会话/凭据/Embedding，也不代表当前采集机数据量。保留所有分片原始字节和来源声明。

按 [快照说明](../backend/docs/public-intelligence-snapshots.md) 在后端环境校验、dry-run、导入和核对；现已可直接从 `main` 取得，无需另外检出旧后端分支。导入后，仍要让 Agent 消费知识事件建立本地索引。

后端还提供 `scripts/sync-public-snapshot-agent.py`，通过 `--agent-root`、`--agent-python`、`--backend-url` 指向已有 Agent 安装；参数和示例见该脚本与快照说明。不要复制别人的 `.env`、PostgreSQL 数据卷或用户会话库。

## 6. 其他已有入口与验证边界

`scripts/run_fullstack.py` 会优先使用仓库内 `frontend/` 与 `backend/`，保留旧 `reports/local/integration/` 回退方式。该启动器依赖其文档所列本机 PostgreSQL/Redis 二进制和运行环境，不能与已启动的 Docker 后端重复占用同一端口，参见 [本机集成历史说明](fullstack_local.md)。

本次集成只核对源码、目录、参数与公开内容，未重跑功能测试、采集或付费模型调用。各历史报告的通过数字按日期和测试范围读取，不能作为新部署的验收结果。
