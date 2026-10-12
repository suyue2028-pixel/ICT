# LLM 语义分类与通用搜索验收（2026-10-12）

## 问题与修改

原实现依靠限定句式识别日常交流，“我喜欢你”未匹配后进入专业检索；旧对话安全过滤还拒绝普通数字。现在 live 请求先调用平台模型 `classify`，返回 `intent/entities/information_needs/freshness_required/conversation_answer`。一般交流在同次调用中直接回答，不加载文档 scope、索引快照或检索工具。知识库是否命中不决定专业与否。

专业问题继续 LangGraph、精确 CVE / BM25 + E5、证据相关性、需求覆盖、受控补检、生成和引用验证。新增 `web_search` 固定使用 Tavily 后端服务；原 NVD、CISA KEV、OSV、OWASP 保留。检索失败不模拟成无漏洞或真实搜索成功。

历史 CVE 仅来自最近相关用户主题；多目标指代必须澄清，不能从助手历史或更早已切换的主题取事实。所有模型调用与有限格式修复共享默认6次预算。核验索引必须从0开始，错误格式最多受控修复一次，鉴权异常不走格式重试。

没有来源时，稳定概念可提供“模型通用解释；未经过来源核验。”，公开 `retrieval_mode=llm_unverified/status=partial`，没有来源、引用、CVE、结构化事实或已核验覆盖。具体产品漏洞、版本、评分、利用状态、官方修复及实时信息禁止回退。此守卫较保守，未核验专业解释中的十进制例子也可能被拒；普通对话中的算术不受影响。

## 自动测试：Mock 与隔离数据库

| 范围 | 结果 | 口径 |
| --- | --- | --- |
| Agent + 评测契约 | 185 passed | 包括语义首分类、空库日常、混合CVE、历史防伪、搜索补充、引用与预算；合成模型和临时资料 |
| 本机原后端 | 227 passed，1 warning | 供应商/搜索Mock；数据库回滚 |
| 保留同学功能的合并后端 | 301 passed，1 skipped，1 warning | 临时独立PostgreSQL Schema，结束清理；供应商/搜索Mock；跳过项保留上游标记 |
| 前端单元测试 | 20 passed | 日常无引用面板、未核验解释、搜索/模型/用户鉴权错误区分及恢复 |
| Vue/TypeScript/Vite构建 | passed | 首页已有589kB chunk警告仍在，不影响构建成功 |

没有新增依赖。测试未向真实库写入合成搜索结果；Mock通过不能表示供应商或Tavily真实调用成功。

## 真实运行

通过 Vue 同源代理 → FastAPI 普通用户鉴权 → Agent → 平台 DeepSeek，最终一轮 **39/39检查通过**。这只是有限场景验收，不是模型整体准确率。

| 输入 | 实际结果 |
| --- | --- |
| 你好、我喜欢你、你是谁、2+2等于几、今天天气真好 | 5项均HTTP200/live，1次模型调用、0次工具，无引用；约0.86～1.86秒 |
| 你好，请解释 CVE-2021-44228 | 精确查询与真实来源；3次模型调用，约5.62秒 |
| 它是否有在野利用记录？ | 历史目标解析成功，实际KEV/NVD工具，真实来源；约6.55秒 |
| LLM04:2025 Data and Model Poisoning | 本地OWASP资料、真实生成和引用；partial保留证据边界；约8.38秒 |
| QKD一般安全机制和局限 | 本地无支持证据，实际web_search失败`search_not_configured`；真实模型一般解释、llm_unverified、0来源；约8.53秒 |

8个公开证据摘录均逐字核对真实后端文档、原始修订hash与定位。未登录仍401；普通用户不能读取内部模型配置；公开响应中没有平台凭据。网页实际发送“我喜欢你”显示“基础对话 · 大模型回答”。

初次真实验收发现未核验解释的内部模型没有转换为公共契约，造成500/502；已修复序列化并增加非空需求/检索摘要回归。随后一次OWASP核验输出格式错误返回503；已收紧提示和增加一次预算内格式修复，最终场景通过。真实模型仍可能失败或拒绝，前端继续显示真实错误。

原始记录在Git忽略的 `reports/local/fullstack/semantic_live_{validation,captures}.json`，早期失败记录保留initial后缀。截图在 `reports/local/fullstack/semantic_qa_20261012.jpg`。

## 上传与同学代码兼容

Agent、后端、前端分别维护 `agent-dev`、`feature/backend-integration`、`codex/frontend-demo`。对后端从当前GitHub头进行三方合并，保留资料质量、更新时间、原始版本证明、调用统计、失效与外部索引ACK等功能；解决3处源码/测试冲突。合并版已单独测试，并同步到本机运行副本。

新完整分支源码包含实现。`semantic_intent_v1.patch`与`semantic_routing_v1.patch`保留用于明确的上一阶段运行基线迁移，不应直接覆盖同学其他版本；旧dynamic补丁保持原样。从已发布的完整分支启动时无需重复安装这些补丁。

## 接入契约与配置

- Vue仍只请求业务后端 `/api/v1/agent/query`，携带普通用户Bearer；不接触供应商密钥。
- 后端内部模型接口新增 `purpose=classify/general_explain`，只接收服务令牌。分类5字段严格校验；general必须提供非空直接回复，专业分类回答必须null。
- 日常公开结果为 `response_kind=conversation/retrieval_mode=conversation`；专业结果仍携带真实evidence/source/tool_trace。
- 无来源的一般解释为 `retrieval_mode=llm_unverified/status=partial/question_type=general_explanation`；必须有固定NOTICE，全部需求missing且无引用信用。
- 内部情报接口新增 `tool=web_search,args={query}`；不接受调用者URL、Key或结果数量。最多3个网页结果；供应商raw正文标partial，摘录标summary，不声称独立抓取/完整核验。
- Tavily Key仅后端 `TAVILY_API_KEY`或`TAVILY_API_KEY_FILE`；本机启动器识别私密 `.secrets/tavily-api-key`。不能写入VITE变量或Git。
- 九种搜索错误码与平台模型错误、用户过期分开；来源URL需HTTPS、公网DNS、标准端口，无凭据。资料/网页中的指令始终按不可信内容处理。

```sh
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/run_fullstack.py start --live --detach
PYTHONPATH=packages/rag_agent/src .venv/bin/python -m pytest packages/rag_agent/tests evaluation/test_dynamic_evaluation.py -q
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/verify_semantic_fullstack.py --allow-live
```

## 未验收项

本机缺Tavily Key，**真实通用网页搜索尚未验证**；DeepSeek平台Key不等于搜索Key。Qwen真实调用缺其供应商凭据，只验证了Provider替换的Mock契约。还需要独立人工答案质量评测、生产部署/登录与负载验证。有限自动验收不能替代这些工作。
