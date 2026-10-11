# 专业问答的前端状态契约

本轮修改基于正在运行的 Vue 前端，保留基础对话、原文证据和来源版本链接。可移交补丁为 `integration/frontend_patches/conversation_v1.patch`，相对 GitHub 项目根目录使用 `frontend/src/...` 路径；它包含基础对话与本轮状态展示的全部增量。

## 接口字段

`POST /api/v1/agent/query` 继续使用 V1 返回值：

- `status`：`completed`、`partial` 或 `insufficient_evidence`。
- `response_kind`：`knowledge` 或 `conversation`；缺少字段时兼容 `retrieval_mode=conversation`。
- `coverage`：可选，形如 `{"answered_topics": ["文件访问边界"], "missing_topics": ["审计日志覆盖"]}`。省略时前端不推断覆盖情况。
- `partial_errors`：兼容原有提示，并识别 `model_generation_failed`、`relevance_check_failed`、`claim_support_check_failed`、`coverage_incomplete`、`retrieval_failed`；代码可带冒号后的说明。
- `X-RAG-Answer-Mode` 与 `X-RAG-LLM-Calls`：继续用于标明实际生成方式，不能用 `status=completed` 推断调用过模型。

## 展示优先级

| 实际情况 | 前端展示 |
| --- | --- |
| 基础对话 | 基础对话及原有大模型／本地回复标签，不要求引用 |
| 模型生成、相关性或事实核验失败 | 模型服务异常；有原文证据时明确当前仅显示资料证据 |
| 检索失败 | 检索服务异常，不宣称“知识库没有相关资料” |
| 正常检索无证据 | 资料不足 · 证据不足 · 未生成资料回答 |
| `partial` | 部分资料回答；有 `coverage` 时分别列出已有证据支持与尚未核实的范围，后者可能包括资料不足、截断或引用重建后撤销的内容 |
| 有证据、未调用模型 | 资料摘录 · 未调用大模型生成 |
| 有证据、模型生成 | 大模型回答 |
| HTTP 503 的 `model_relevance_failed`、`model_verification_failed`、`model_generation_failed` | 请求异常 · 模型服务异常 · 未生成回答 |
| HTTP 429 或 `agent_rate_limited` | 请求受限 · 请稍后重试 |
| HTTP 502／其他 503 | 问答服务异常 · 未取得回答 |
| HTTP 504／客户端超时 | 回答超时 |

请求错误通过 `errorMessage()` 显示中文说明，三个模型错误和索引／检索错误明确说明失败不代表没有相关资料；未知代码仍保留原错误代码，不伪造回答或引用。请求失败不显示空引用提示。

## 会话恢复与示例问题

生成方式、状态、覆盖主题与提示存于本浏览器的 `sessionStorage`，供刷新后的历史消息恢复。后端历史接口目前不持久返回这些元数据；更换浏览器或清除存储后，旧消息的生成方式标为未记录，也不推断其 `status` 与覆盖主题。原先保存为“资料摘录”但没有引用的历史专业回答改为“历史消息 · 未记录可核验引用”，不猜测原始回答状态。

示例问题由已经加载的当前资料范围生成，优先选择有效 CVE 编号和非漏洞资料标题。资料正在加载、加载失败、为空或没有适合生成示例的记录时，只推荐基础能力询问。前端资料列表与 Agent 的同步索引可能存在延迟，因此示例不承诺资料齐全，页面注明可回答范围以实际检索证据为准。

## 本轮工作范围

本轮只修改本地运行前端并提供补丁，没有运行构建、自动测试、模型请求或浏览器操作。尚不能将静态修改视为全量运行验收。后端必须先兼容可选 `coverage` 字段，再部署输出该字段的 Agent。
