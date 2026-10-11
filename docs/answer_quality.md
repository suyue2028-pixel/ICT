# Agent 具体回答与证据边界

## 本轮改动（2026-10-10）

- 本机原进程为 `extractive`，没有调用 DeepSeek。现在私密凭据通过服务端文件加载，Agent 已切换为 `live`；凭据没有进入版本控制。
- 从上游 PR #3、`feature/agent-qa-relevance` 的 `8bceeb9c1ac653b9a107487412726aa4806fb6dc` 导入六个核心文件的改动，补入问题相关性筛选、检索主题约束、历史指代处理和启动时检索模型预加载。Git CLI fetch 的身份认证未就绪；本地 HEAD 仍是 `agent-dev` 原提交，并不声称已经切换到该上游提交。
- 本轮进一步修改 `nodes.py` 与 `providers/llm.py`，新增 `presentation.py`，按问题决定机制、条件、影响、动作及未知项的呈现要求。每条结论需要明确对象和行为；资料没有支持时不编造细节。
- 评分保留版本及评分主体；影响范围、处置记录和缺失信息使用可读中文，不直接将内部 UUID、字段名或 JSON 当成回答。
- 编号只出现在源记录的别名字段时，提供原始字段证据。它只支持编号归属，漏洞机制仍需同时引用正文。没有把内部标准化的编号对象当成源引用。
- 原始 OSV 范围事件与 CPE 条件不等于资产匹配结果。增加常见版本比较与泛化措辞的保守拦截；这不是完整的版本范围解析器。单条结论被拒绝时保留其他合法结论，继续语义核验并返回 `partial`。
- 生成、相关性筛选及核验共享最多 3 次 HTTP 请求。live 路由和术语改写使用确定性规则；核验不能进行时回到明确标记的摘录。

## 真实问答记录

通过本机业务后端 `POST /api/v1/agent/query`、普通用户身份、`is_demo=false`，三次运行同一个现有问题：

> CVE-2023-36258 是什么漏洞？涉及哪个软件包和组件、通过什么调用触发、攻击者能造成什么后果？请说明现有来源能确认的内容和未确认事项。

| 运行阶段 | HTTP | 实际回答模式 | 模型请求次数 | 耗时 | 观察 |
| --- | --- | --- | --- | --- | --- |
| 初次接入 | 200 | extractive | 1 | 3.730 秒 | 收到生成后被输出校验拒绝，返回摘录；不是完整 live 成功 |
| 补入原始编号字段引用 | 200 | live | 2 | 4.648 秒 | 真实生成与核验成功，但仍出现未经求值的版本范围和升级推断 |
| 加入范围拦截与逐条保留 | 200 | live | 2 | 5.152 秒 | 删除不合格的修复／版本范围结论，保留具体机制、组件及源记录版本；最终状态 partial |

最终回答指出 langchain、PALChain、python exec 与任意代码执行的关系，并标明利用输入、攻击者权限、实际部署可利用性、评分和 KEV 信息未确认。没有返回“某版本或更高均安全”的升级承诺。

原始脱敏响应在被 Git 忽略的 `reports/local/strict_audit_20261010/` 中：

- `deepseek_live_demonstration.json`
- `deepseek_live_demonstration_after_fix.json`
- `deepseek_live_demonstration_final.json`

真实来源为 OSV 的 GHSA 与 PYSEC 记录；此次没有查询 CISA KEV，也没有核验具体资产。本轮只进行了现有问题的真实运行演示与静态审阅，没有运行回归测试、广泛题库评测或计算准确率。最终演示后扩充了拦截规则中的比较表达覆盖范围，并重新加载进程；没有追加模型演示。

## 本机运行

```sh
.venv/bin/python scripts/run_fullstack.py restart-agent --live
.venv/bin/python scripts/run_fullstack.py status
```

只重启 Agent。完整首次启动方法见 [fullstack_local.md](fullstack_local.md)，私密配置见 [llm_configuration.md](llm_configuration.md)。旧会话中的旧回答不会被改写；从前端发送新问题才使用新链路。

## 后端接入契约

- 保持 `/v1/query` V1 请求与响应字段；前端通过业务后端 `/api/v1/agent/query` 调用，不传模型密钥。
- `limits.max_tool_calls`、`limits.max_answer_tokens` 继续生效；模型 HTTP 尝试，包括失败和重试，计入总预算。
- `X-RAG-Answer-Mode` 为实际回答模式，`X-RAG-LLM-Calls` 为请求次数；不能只依赖 health 中的配置模式。
- `partial_errors` 可说明生成回退、字段缺失、被拒绝的结论和核验失败；`partial` 必须在页面保留，不能包装成完全确认。
- `evidence` 继续绑定后端文档 UUID、内容 hash 和原始版本；原始编号字段引用仍须通过版本引用校验。
- `【推断】` 与 `【待核实】` 已写入回答正文。随后基础对话增加 `response_kind` 和 `retrieval_mode=conversation`，需前后端合同同步；见 [basic_conversation.md](basic_conversation.md)。

## 尚需补充

本机仅 47 条真实资料，且库内最新发布记录仍停留在 2026-07-13。提示词不能弥补资料缺口。广泛的 AI 风险分类、工具审计、部署条件、最新漏洞和官方修复步骤，需要更相关且持续更新的材料。NVD、CVE、CISA KEV 的新增按需核查工具未在本轮实现；字段冲突裁决和独立人工质量评测仍需完成。配置及提示词改进不代表 Agent 已完全完成。
