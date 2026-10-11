# 专业问答范围与失败状态接入契约

本次在现有后端 V1 契约上增量增加 `coverage`，保留基础对话和资料引用核验，不增加默认的无证据模型回答。

## 响应字段

Agent 内部 `QueryResponse`、Agent `/v1/query` 和业务后端 `/api/v1/agent/query` 使用相同的字段形状：

```json
{
  "response_kind": "knowledge",
  "status": "partial",
  "coverage": {
    "answered_topics": ["工具权限与访问边界"],
    "missing_topics": ["调用日志与可追溯性"]
  }
}
```

- `answered_topics`、`missing_topics` 缺省均为空数组；每个数组最多 12 项，每项为 1 至 120 个字符的字符串。
- `coverage` 是当前回答的范围说明，不是额外的事实、资料、断言或引用。不得用它绕过文档范围、文档版本、来源 URL、原文摘录及 CVE 来源关联校验。
- 只有最终保留的有来源结论与可核验摘录同时存在时，V1 适配器才允许传递 `answered_topics`。具体主题归属由 Agent 在相关性判断和结论核验后确定；检索命中本身不表示已经回答。
- 没有证据时状态为 `insufficient_evidence`，`answered_topics` 必须为空，可通过 `missing_topics` 说明未核实范围。
- 有部分可信结论、但仍有缺失范围时，Agent 返回 `partial`。后端仍要求 `sources` 和 `evidence` 非空，并执行原有来源核验。
- 原始版本引用核对失败、答案被重建为来源摘录时，状态为 `partial`，原 `answered_topics` 去重并入 `missing_topics` 后清空；合并后的数组仍最多 12 项。如果所有引用均被移除，则为 `insufficient_evidence`。
- 没有最终结论、没有证据或传输长度导致答案截断时，同样将原已回答主题转入尚未核实的范围，避免宣称被撤下或截去的主题仍已回答。
- `missing_topics` 统一表示当前回答尚未核实的范围，不等同于“数据库没有该类资料”。引用验证或正文长度限制也可能让主题从已回答范围退出。
- V1 每条公开证据最多 2000 个字符。内部证据超过该限制时，保留通过原始版本核对的前缀作为真实摘录，并记录 `transport_evidence_truncated`；该证据不再用于保留模型结论或结构化事实。答案保守重建为来源摘录并撤销已回答主题，避免模型依据内部后半段生成的结论缺少公开引用支持。风险评级仅从引用未截断且仍有效的来源字段计算，否则保持 `UNKNOWN`。
- 基础对话必须返回空 `coverage`，继续要求原始查询整句匹配基础对话、没有专业来源、工具、证据、漏洞或风险结果。

V1 适配器兼容尚未带 `coverage` 的旧内部结果，以空对象处理。可选范围元数据格式不合格时保守省略，不影响已经通过来源核对的正文。

## 失败状态

“没有匹配证据”与“模型判断失败”分别表示不同情况：

| 情况 | Agent V1 HTTP 状态与 detail | 业务后端行为 |
| --- | --- | --- |
| 无可核验资料 | 200，响应 `status=insufficient_evidence` | 正常保存响应，显示证据不足 |
| 有可信结论但覆盖不完整 | 200，响应 `status=partial` | 正常保存响应与来源，显示缺失范围 |
| 模型相关性判断失败且没有最终可信结论 | 503，`model_relevance_failed` | 仅转发该安全协议代码，记录 invocation 失败 |
| 模型结论核验失败且没有最终可信结论 | 503，`model_verification_failed` | 同上 |
| 模型生成失败且没有可用结果 | 503，`model_generation_failed` | 同上 |
| 模型生成失败但仍有经过核验的原文摘录 | 200，`partial`，错误列表含 `model_generation_failed` | 显示来源摘录并披露模型失败，不能标成成功的大模型回答 |

模型异常的三种公共代码采用严格白名单。后端最多读取 4096 字节错误响应，不转发任意供应商错误正文。原有超时、调用预算、服务不可用和契约验证失败仍走独立 HTTP 异常。

实际生成方式仍由 `X-RAG-Answer-Mode`、`X-RAG-LLM-Calls` 和原有 `generation_mode:*` 说明，不能根据 `coverage` 推断模型调用成功。

## 存储与恢复

当前后端将完整响应写入现有 `AgentInvocation.response_payload` JSON，因此本次不需要数据库迁移。会话消息列表接口仍只提供正文、引用和 `retrieval_mode`，不会自动恢复 `coverage`。前端不能从缺少该字段的历史记录猜测已经回答的主题。

## 同步顺序

1. 先升级后端 `backend/schemas/agent.py` 与 `backend/services/agent_gateway.py`。
2. 再升级 Agent 契约、V1 适配器和问答节点。
3. 最后升级前端范围与异常展示。

新的后端允许旧响应省略 `coverage`；旧后端使用 `extra=forbid`，会拒绝新增字段，所以不能只升级 Agent。此处保留 schema_version `1`，属于双方需要同时协调的可选字段扩展。

累积后端补丁是 [conversation_v1.patch](../integration/backend_patches/conversation_v1.patch)，同时包含之前的基础对话保护与本次范围字段、失败代码处理。补丁以 `reports/local/conversation_backend_before` 的原始文件为基线；在已有上游改动的分支上应先审阅冲突，保留同学的其他代码。源码分支及发布版本记录在 `docs/fullstack_branches.json`。

## 本次实现与验证边界

已完成字段约束、范围保守透传、失败代码白名单及累积补丁生成，并以静态阅读核对原有来源和基础对话保护。本文不声称自动化测试、构建、模型调用或端到端演示已经通过；运行结果由实际运行记录单独报告。
