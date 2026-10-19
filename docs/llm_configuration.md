# 平台大模型配置与 Agent 接入

## 当前架构

浏览器仅请求业务 FastAPI。平台供应商密钥仅由业务后端读取；Agent 通过认证内部接口使用平台默认模型，前端和知识库同步进程不接收供应商密钥。正常用户登录后可使用问答，不需要配置 API Key。

默认保留 DeepSeek Flash。后端另有千问适配和能力声明，切换聊天 Provider 不修改 E5 配置、已有向量、文档或证据编号。千问是否真实可调用，仍需独立的千问账号与真实验证；HTTP 测试替身通过不能作为供应商实际接通证明。

2026-10-10 的 DeepSeek 问答是旧链路历史记录。2026-10-11 新的平台内部 transport 已完成独立真实问答验证，运行记录为 `reports/local/fullstack/dynamic_live_validation.json`，涵盖普通用户授权、真实模型问候、CVE 来源及动态工具结果的引用核对。Provider 错误处理和千问切换另有隔离上游的 Mock 测试；不能把这些测试写成千问真实调用成功。

## 后端配置

下列设置只交给业务后端进程，由管理员设置，不写入 Vue 的 `VITE_*` 或 Git：

```sh
export LLM_PROVIDER=deepseek
export LLM_MODEL=deepseek-flash
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY_FILE="$PWD/.secrets/llm-api-key"
```

也可使用 `LLM_API_KEY`；不要与 `LLM_API_KEY_FILE` 同时配置。后端兼容团队已有 `DEEPSEEK_API_KEY` 和千问的 `DASHSCOPE_API_KEY`。实际密钥文件须为私密普通文件，供应商密钥和三个内部模块之间的认证令牌用途不同。

千问由后端设置 `LLM_PROVIDER=qwen`，选择后端能力清单内的模型及相应 Base URL。后端明确声明 JSON 与服务端工具工作流能力。当前内部接口不接受原生模型工具调用；需要该能力的请求明确失败，Agent 原有的确定性工具执行继续由工作流负责。

## Agent 配置

```sh
export RAG_LLM_MODE=live
export RAG_LLM_TRANSPORT=backend
export RAG_BACKEND_URL=http://127.0.0.1:8010
# RAG_BACKEND_TOOL_TOKEN 与后端 AGENT_TOOL_TOKEN 使用同一个私密值。
# 此 token 用于内部认证，与供应商 API Key 和用户登录 token 不同。
```

`RAG_LLM_GATEWAY_URL` 可覆盖内部地址，`RAG_LLM_SERVICE_TOKEN` 可显式覆盖模型内部认证令牌。平台模式拒绝 `RAG_LLM_KEY` / `RAG_LLM_KEY_FILE`，不读取供应商密钥文件。不要给此 Agent 进程导出后端的 `LLM_API_KEY`。

内部协议：

```text
POST /api/v1/internal/llm/complete
X-Agent-Token: <内部服务令牌>

{purpose, payload, max_attempts, timeout_seconds, max_answer_tokens}
→ {result, attempts, usage, provider, model}
```

Agent 保持 `complete(purpose, payload, budget, timeout)` 接口。后端维护各 purpose 的可信 System Prompt，不接受前端提供供应商 URL、API Key 或 System Prompt。每次模型实际 HTTP 尝试计入请求预算；调用成功和失败均携带 attempts。`usage` 仅在供应商返回相应 Token 计数时记录，缺失计数不伪造为零。

## 本地启动

```sh
# 默认平台模型。启动前预检配置，检查内部模型能力，不付费调用模型。
.venv/bin/python scripts/run_fullstack.py start --detach

# 来源摘录模式：不请求远程模型，但外部资料工具仍可能访问互联网。
.venv/bin/python scripts/run_fullstack.py start --extractive --detach

# 完全限定为本地已有资料时，同时关闭按需外部工具。
RAG_EXTERNAL_TOOLS_ENABLED=false .venv/bin/python scripts/run_fullstack.py start --extractive --detach

# 后端已经更新并重启、内部模型能力接口可用时，单独重启 Agent。
.venv/bin/python scripts/run_fullstack.py restart-agent --live
```

启动器将旧 `RAG_LLM_KEY(_FILE)` 或已有 `.secrets/llm-api-key` 迁移为后端的 `LLM_API_KEY(_FILE)` 配置，不读取文件内容。Agent、同步进程和前端进程会移除供应商密钥及文件路径；Vite 服务进程同样移除密钥变量。改变后端 Provider 或 Key 后须重启后端，单独重启 Agent 不会更新运行中的后端配置。

readiness 只证明配置/进程已加载。新发送的问答结果、模型调用计数、来源核验和错误标识才证明回答的执行情况；已有聊天不会自动重新生成。

## 明确的错误类型

| 安全错误码 | 含义 |
|---|---|
| `platform_model_auth_failed` | 供应商 API Key 或权限错误 |
| `platform_model_not_configured` | 平台模型尚未配置 |
| `platform_model_rate_limited` | 供应商限流 |
| `platform_model_timeout` | 模型或内部模型请求超时 |
| `platform_model_unavailable` | 模型/内部服务连接异常 |
| `platform_model_format_invalid` | JSON、完整性或响应协议不合法 |
| `platform_model_capability_unsupported` | 所选模型不支持所需受控能力 |
| `platform_internal_auth_failed` | Agent 到后端的内部认证失败 |

这些都不表示用户会话过期。只有业务接口实际返回用户认证错误时，前端才引导重新登录。错误不携带供应商正文、请求头、Key 或任意异常字符串。

## 预算与离线兼容

默认配置为最多 5 次模型尝试、10 次工具调用、3 轮检索；后端或单次请求传入更低限制时取更小值。`assess` 为生成与核验保留尝试次数，`synthesize` 为核验保留次数，重试不会静默占用保留额度。设置上限分别是 8、10、3，不代表每次问题都会用满。

环境变量：`RAG_MAX_LLM_CALLS`、`RAG_MAX_TOOL_CALLS`、`RAG_MAX_RETRIEVAL_ROUNDS`。独立 Agent Settings 和根 `.env.example` 的外部工具默认关闭；完整启动器在未显式设置时默认使用 `RAG_EXTERNAL_TOOLS_ENABLED=true`，显式 false 优先。后端必须真实包含并运行注册的情报接口，未接入工具不能模拟成真实查询。

独立开发保留 `RAG_LLM_MODE=extractive|mock`。旧的供应商直连仅可显式配置 `RAG_LLM_TRANSPORT=direct`，配合 `RAG_LLM_KEY(_FILE)`；完整平台启动器始终使用后端统一模型接口。mock 只用于流程测试，真实证据不足不自动改为模型记忆回答。聊天模型配置独立于 Embedding，切换 Provider 不重建 E5 索引。

用户访问凭证由 `/login` 授权页验证，模型 Key 由管理员仅配置在后端。授权页不签发令牌，问答页保留过期授权下的草稿与失败问题；这些机制不能替代未来的生产账号登录。运行副本准备、conversation → dynamic 补丁顺序、服务启动和旧 extractive 验证方法见 [fullstack_local.md](fullstack_local.md)。

后端接入不新增依赖，沿用已有 httpx。HTTP 关闭重定向和环境代理，响应有大小限制。真实引用与事实校验继续由现有 Agent/RAG 工作流执行。
