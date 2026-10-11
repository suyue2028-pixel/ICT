# 基础对话与资料问答

## 行为

2026-10-10 增加基础对话路径，解决“你好”被当成知识检索、随后显示证据不足的问题。

- 问候、感谢、告别、自我介绍、能力说明和限定的简单日常交流使用基础对话。例如“你好”“你好亲”“你能做什么”“你在干嘛”“我有点累”。
- live 配置下由 DeepSeek 生成自然回复，不检索知识库、不添加引用。模型不可用或输出不合格时提供本地友好回复，并保留实际生成模式。
- 专业问题继续检索与核验。“你好，某漏洞怎么修复”不能只因以“你好”开头就变成基础对话。
- 专业历史和资料不发送给基础对话模型。资料问答解析“它”时，会略过中间的问候和感谢，继续寻找用户之前提出的专业对象。
- 前端分别显示“基础对话”和“资料回答”；基础对话不显示无引用警告。等待消息采用“整理回复”，无需假装正在检索。

这条基础路径采用整句意图匹配，并非通用聊天的完整语义路由。没有匹配到的表达仍走知识问答。基础回复的数字、网址和引用过滤也是保守规则，不能代替所有专业事实的语义判断。

## 接入契约

Agent 内部及后端 V1 响应新增：

```json
{
  "response_kind": "conversation",
  "status": "completed",
  "retrieval_mode": "conversation",
  "sources": [],
  "evidence": [],
  "cve_ids": [],
  "risk_level": "UNKNOWN",
  "tool_calls": [],
  "tool_trace": []
}
```

`response_kind` 缺省为 `knowledge`。知识回答保留原有文档范围、版本、摘录、引用和硬事实约束。后端仅在原始查询匹配限定基础对话、所有专业结果为空时，接受无引用的完成回复。

生成模式仍通过 `X-RAG-Answer-Mode` 标识，`X-RAG-LLM-Calls` 记录实际 HTTP 尝试次数：

- `live`：真实模型回复。
- `extractive` 且 `conversation_local_reply:no_remote_llm`：本地回复，未调用模型。
- `extractive` 且 `conversation_local_reply:after_remote_attempt`：模型调用后降级为本地回复。

会话历史使用现有 `retrieval_mode` 保存对话类型，不需要数据库迁移。

## 当前运行演示

- 在现有浏览器问答页输入“你好”，页面显示“基础对话 · 大模型回答”，并正常返回问候与需求邀请，没有资料不足或无引用提示。截图：`reports/local/strict_audit_20261010/conversation_hello.jpg`。
- 经业务后端发送“你能做什么？”：HTTP 200、`completed`、`conversation`、真实模型 `live`、1 次 HTTP 请求、约 0.81 秒；来源与证据均为零。脱敏记录：`reports/local/strict_audit_20261010/conversation_capabilities_live.json`。
- 上述为真实运行演示与静态审阅，没有运行自动化测试或构建。演示后收紧了回复中的实现术语说明，并限定模型名称仅在用户询问时传入；随后重新加载 Agent，没有追加供应商演示。

## 同步给同学

本机 Agent、后端 API 和前端已同步。后端和前端运行副本在 Git 忽略目录中，额外保存可提交到各自分支的精确补丁：

- `integration/backend_patches/conversation_v1.patch`
- `integration/frontend_patches/conversation_v1.patch`

在拥有对应代码的仓库根目录，审阅后应用所需补丁：

```sh
git apply integration/backend_patches/conversation_v1.patch
git apply integration/frontend_patches/conversation_v1.patch
```

两份补丁可以分别交给对应分支维护者。它们是基于本机当前运行副本生成的，不覆盖上游之后的其他改动。后端模块 `agent_conversation.py` 与 Agent 的 `agent/conversation.py` 意图及输出规则应保持同步。

部署顺序：先升级后端合同与校验，再升级 Agent，最后更新前端。旧后端的严格 JSON 模型会拒绝新增字段，不能只上线 Agent。

源码分别维护在 `feature/backend-integration`、`agent-dev` 和 `codex/frontend-demo`；发布版本记录在 `docs/fullstack_branches.json`。本地运行记录保留在 Git 忽略目录中，文档中的指标摘要可用于交接，密钥和浏览器会话不随代码发布。
