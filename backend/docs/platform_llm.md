# 平台模型与用户权限接入

## 当前架构

Vue 使用用户 Bearer Token 调用 `/api/v1/agent/query`。后端校验用户身份、预算和会话，向独立 Agent 服务发送受控请求。Agent 的工具、检索、证据核验保持独立；聊天生成通过内部 `/api/v1/internal/llm/complete` 交给后端的平台 Provider。密钥只由后端读取。

旧管理员 `/api/v1/qa/ask` 也使用同一 Provider、配置和错误分类，其原检索与 `status/answer/citation_indices` 协议继续保留。这条旧接口没有独立 LangGraph 动态工具工作流，应优先使用 `/agent/query`。旧 `deepseek` 会话类型兼容保留，新建管理员会话使用 `platform`，无需迁移知识库。

## 配置

| 变量 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | `deepseek` 或 `qwen`，默认 `deepseek` |
| `LLM_MODEL` | 默认 DeepSeek `deepseek-flash`，Qwen `qwen-plus` |
| `LLM_API_KEY_FILE` | 私有普通文件，权限 `0600` / `0400`，不超过 4096 字节，禁止符号链接 |
| `LLM_API_KEY` | 环境注入的通用平台 Key，优先于文件 |
| `DEEPSEEK_API_KEY` | DeepSeek 旧配置兼容 |
| `DASHSCOPE_API_KEY` / `QWEN_API_KEY` | Qwen 服务商凭据兼容 |
| `LLM_BASE_URL` | 管理员设置的 HTTPS Chat Completions Base URL；不允许用户名、密码或查询串 |
| `LLM_TIMEOUT_SECONDS` | 本次调用总时间上限，默认 30 秒，最多 120 秒 |
| `LLM_MAX_ATTEMPTS` | 每次最多 1～2 次上游 HTTP 请求，由 Agent 剩余调用预算进一步限制 |
| `QA_PROVIDER` | 旧管理员接口使用 `auto` / `platform` / `external`；旧 `deepseek` 作为平台路径兼容别名 |

Embedding 的模型、Key 和向量签名由原独立配置管理。改变聊天 Provider 不触发重建向量库。用户不能通过请求覆盖模型、Base URL、平台 Key 或可信 System Prompt。

百炼的 Key 与 Base URL 必须属于同一区域/工作空间。模型名采用显式能力配置：当前 DeepSeek `deepseek-flash`、`deepseek-v4-pro`、`deepseek-chat`，Qwen `qwen-plus`、`qwen-flash`、`qwen-turbo`、`qwen-max`。新模型名需要先更新能力表并验证，未知模型明确报错。

## 内部调用协议

内部调用使用现有 `X-Agent-Token` 的服务令牌（`require_tool`），不是普通用户 Token。内部端点不通过前端 CORS 开放服务凭据，也不返回平台凭据或文件路径。

`POST /api/v1/internal/llm/complete` 请求：

```json
{
  "purpose": "synthesize",
  "payload": {"question": "本轮问题", "evidence": []},
  "max_attempts": 1,
  "timeout_seconds": 20,
  "max_answer_tokens": 1200
}
```

成功响应包括 `result`（JSON 对象）、`attempts`、`usage`、`provider`、`model`。`usage` 仅记录供应商实际返回的 Token 数，缺失时是空/未知，不推算为真实用量。日志仅记录用途、配置模型、尝试次数和用量，不记录问题、证据正文或供应商错误体。

用途集合为 `classify/general_explain/plan/rewrite/assess/synthesize/verify/converse`；`legacy_answer` 为兼容旧管理员引用协议的受控用途。后端固定可信 Prompt；用户、历史、检索片段均视为数据。`classify`、`converse`、`general_explain` 和旧管理员接口传递经过角色/长度校验的原生用户与助手消息，最多八条、每条最多 2000 字符；其他用途继续保留原 Agent 数据协议。

### 语义分类与回答类型

live 请求首先使用 `classify` 理解当前消息与用户历史。分类器不是关键词列表；它可以在同一次调用中直接回答日常交流、喜好、算术或一般常识。内部返回严格结构：

```json
{
  "intent": "general",
  "entities": {"cve_ids": [], "products": []},
  "information_needs": [],
  "freshness_required": false,
  "conversation_answer": "2+2=4。"
}
```

`intent` 只有 `general/security/clarification`；实体各最多八项，信息需求最多八项，时效字段必须为布尔值。普通问题必须在同一次分类中给出非空 `conversation_answer`，不再追加第二次日常回复调用；安全问题和待澄清问题的直接回答字段必须为 `null`。实体只能来自当前用户消息，或在本轮明确指代时来自用户历史；历史助手回答不构成事实来源，Agent 还会核对实体出处。

普通回答保留 `response_kind=conversation`、`retrieval_mode=conversation`，没有来源、证据、CVE、风险评级或工具调用。允许正常数字，不再要求用户消息匹配旧问候正则。平台仍拦截伪造引用、URL 和具体安全事实。

专业问题优先沿真实工具、RAG 和来源核验链路处理。只有不含具体漏洞、版本、评分、利用状态和时效事实请求的通用概念，可使用 `general_explain` 返回明确标注的解释。其公开契约为：

- `response_kind=knowledge`、`retrieval_mode=llm_unverified`。
- `status=partial`、`question_type=general_explanation`、`risk_level=UNKNOWN`。
- `sources/evidence/cve_ids` 为空，不能声明已支持的覆盖项。
- `limitations` 必须包含 `模型通用解释；未经过来源核验。`。
- 实际执行但未取得可引用依据的工具记录可以保留，不能据此把通用解释包装成已核验事实。

后端单独验证这个分支；正常 `completed` 安全回答仍必须有真实来源和证据。这里的文本边界是保守的格式与事实类型保护，不代表模型解释已经通过语义事实核验。具体 CVE、包版本、CVSS、KEV、官方修复或最新情报需求不能借此绕过来源校验。

`GET /api/v1/internal/llm/capabilities` 返回配置存在性、Provider、模型以及能力：JSON 输出、服务端工具工作流可用，当前传输接口不提供原生模型工具调用。Agent 通过代码执行真实工具并核验 JSON 计划；请求原生工具能力会明确返回不支持，禁止静默丢失关键工具。

DeepSeek 发 `thinking.type=disabled`，Qwen 发 `enable_thinking=false`，不交叉发送供应商参数。仅使用 JSON Object 格式，由 Agent 原有 Pydantic 合约验证语义；不假设两个供应商支持相同 JSON Schema 强制格式。[DeepSeek 文档](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [百炼结构化输出文档](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-structured-output) 支持本次格式选择。

## 错误与鉴权边界

| 公开错误码 | HTTP | 含义 |
| --- | --- | --- |
| `user_token_required` / `invalid_user_token` | 401 | 用户凭证缺失、过期、撤销或不合法 |
| `platform_model_auth_failed` | 502 | 供应商 Key 不合法或缺少账户权限；用户登录未失效 |
| `platform_model_rate_limited` | 503 | 供应商限流 |
| `platform_model_timeout` | 504 | 模型调用超时 |
| `platform_model_unavailable` | 502 | 网络或供应商服务不可用 |
| `platform_model_format_invalid` | 502 | 非 JSON 对象、输出截断、意外工具调用等 |
| `platform_model_capability_unsupported` | 502 | 未审查模型或不支持的调用能力 |
| `platform_model_not_configured` | 503 | 后端模型凭据未配置或私有文件不符合要求 |

内部模型失败响应为 `detail: {code, attempts}`，失败尝试也计入 Agent 调用预算。只对 429/503 在允许预算内重试，401/403 不重试。真实工具故障仍以 Agent/RAG 错误报告，不转换成“资料不足”。供应商任意响应体均不转发。

`GET /api/v1/auth/me` 使用原用户鉴权 SQL，响应 `{owner_id, authenticated: true}`，并禁用缓存。令牌签发与撤销仍要求管理员 Key。当前真实登录入口为输入管理员签发的短期用户令牌，没有用户名/密码、自助注册、平台 API Key 签发或 BYOK 实现。

## 验证说明

本轮新增 Provider、鉴权和错误边界的自动化测试，以及迁移旧管理员引用/多轮测试。上游 Provider 测试使用明确 Mock Transport，数据库业务测试在本机 PostgreSQL 事务内回滚，用户校验单测使用隔离 SQLite 执行真实筛选 SQL。它们不能作为百炼或 DeepSeek 真实调用成功的证明；真实全栈模型调用需单独记录。

从后端目录执行测试时应注入实际运行的服务器配置。其 `.env` 可能属于旧开发数据库；本机一体运行使用根目录私有 `.secrets/fullstack.env`，勿复制内容进 Git 或终端输出。
