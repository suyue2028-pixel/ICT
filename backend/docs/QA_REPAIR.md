# 追溯问答修复与后端契约（2026-10-08）

本机已部署修复：前端 http://127.0.0.1:5173/qa，后端 http://127.0.0.1:8010/docs，外部 Agent 9001。建议新建会话测试；旧历史答案保留。

完整根因、修改清单、七组真实 DeepSeek 对比、测试、评测边界和交接见 [Agent 问答修复记录](https://github.com/suyue2028-pixel/ICT/blob/feature/agent-qa-relevance/docs/qa_repair_windows.md)。当前后端代码仍统一位于本目录，Agent 代码位于 `D:\cit\ICT-team\packages\rag_agent`。

`POST /api/v1/agent/query` 保持原请求兼容，新增可空响应字段：

| 字段 | 含义 |
|---|---|
| generation_mode | live / extractive / mock；缺少可信响应头或旧记录为 null |
| llm_http_calls | Agent 报告且验证范围为 0—3 的实际模型 HTTP 尝试数；旧记录为 null |

两个字段保存到已有调用 JSON 中，`GET /api/v1/agent/invocations/{invocation_id}` 返回相同信息，沿用用户鉴权与会话所有权。幂等重复请求不再次调用模型，并恢复已保存的模式和调用次数响应头。会话历史按原样返回，缺少模式的历史消息保持未知。

检索候选数量和选中数量可从 tool_trace.input_summary、partial_errors 中查看。relevance_filter 是诊断记录；relevance_check_failed、claim_support_check_failed、unsupported_claims_removed 和 claim_support_check_unavailable 明确披露失败或撤回。有引用不代表结论已由独立人工验证。

实际结果：后端 70 项、Agent 86 项回归通过；15 项运行检查通过；七组真实入口请求返回，六组真实 DeepSeek 生成，缺失编号零调用。受影响范围的原始 CPE 尚未解析时仍返回 partial 和未知提示。

没有新增迁移，Alembic 检查一致。API/PostgreSQL/Redis 健康，worker/唯一 beat 运行，数据库和索引保留。配置不需要新密钥。部署备份保存在 output，验证文件在团队副本 reports/local，保持本地排除规则。

```powershell
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py status
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py restart-agent --mode live
```
