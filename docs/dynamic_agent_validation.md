# 动态安全情报 Agent 本轮验收（2026-10-12）

## 已完成与实际条件

本机运行目录为 `/Users/gshy/Desktop/ICT-RAG`。八个管理进程实际运行：Vue/Vite、FastAPI、PostgreSQL、Redis、Celery worker、beat、独立 Agent、索引同步 worker。数据库、原始修订和 E5 索引保留；未重建向量库、未删除真实资料、未新增依赖。官方补检按原管线新增或更新当前修订，并保留历史。

平台真实配置通过内部能力接口确认：`deepseek / deepseek-flash`，Agent `llm_transport=backend`，嵌入 `e5 / live`。默认平台模式只由后端持有供应商 Key。用户采用现有短期令牌授权，不需要模型 Key；不是已实现账号密码注册登录。

## 分阶段结果

| 阶段 | 实际文件与修改 | 原因及结果 |
| --- | --- | --- |
| 诊断与用户授权 | 后端 `api/v1/auth.py`；前端 `api/http.ts`、`views/LoginView.vue`、`router/index.ts`、`utils/requestFailure.ts`、`utils/queryDraft.ts` | 旧用户令牌真正过期，前端保留一次读取的旧值。新增真实身份核验、授权恢复、草稿与失败问题保留；鉴权仍启用 |
| 统一模型 | 后端 `services/platform_llm.py`、`services/llm_prompts.py`、`api/v1/llm.py`、`schemas/llm.py`、`core/config.py`、旧 `services/ai.py`；Agent `providers/llm.py`、`config.py`；`scripts/run_fullstack.py` | 平台模型只从后端调用，兼容旧配置；统一错误、超时重试、usage、能力声明，保留默认Flash。千问仅Mock验证 |
| 动态工作流 | Agent `agent/workflow.py`、`nodes.py`、`graph.py`、`state.py`、`tools.py`、`service.py`；`integration/intelligence.py` | 逐需求与逐CVE归属，注册工具、有限补检、无新证据与预算终止；LangGraph正式节点保存恢复决策 |
| 情报与引用 | 后端 `services/dynamic_intelligence.py`、`api/v1/dynamic_intelligence.py`、`api/v1/router.py`；Agent `integration/api.py`、`contracts.py`、`index.py`、`ingestion/extract.py` | 真实官方结果先形成后端修订再导入E5；返回原文定位，公开引用编号与原始hash双重核验；保留CPE/范围未求解限制 |
| 问答界面 | 前端 `composables/useDemoChat.ts`、`components/chat/ChatConversation.vue`、`ChatComposer.vue`、`types/index.ts`、`utils/answerWorkflow.ts`、`assets/conversation-theme.css` | 真实加载提示、最终工具记录、需求覆盖与限制；无虚假实时执行、无Key输入；去除重复覆盖列表及可见UUID |
| 评测与文档 | `evaluation/dynamic_questions.json`、`scripts/evaluate_dynamic.py`、新增Python/Node回归、JSON schemas、架构/运行文档、两个增量补丁 | 可复现的30题结构回归；Mock、真实供应商与人工语义审核分开 |

前后端文件相对各自 `reports/local/integration/{backend,frontend}`；实际源码已在运行副本中生效。可携带的前后端增量为 `integration/*_patches/dynamic_agent_v1.patch`，应用检查及逐文件比对通过。补丁基线是本阶段已有整链代码，其他同学的新版本需要先比对合并，不应直接覆盖。

## 自动化与隔离测试

| 测试 | 结果 | 实际方式 |
| --- | --- | --- |
| Agent + 评测脚本 Python 测试 | 136通过（Agent129 + 评测7） | 临时SQLite、合成资料、Mock模型/HTTP，包含完整LangGraph恢复、错实体、输出修复、上游错误HTTP分类、两Provider保持索引/工具 |
| 后端完整测试 | 122通过，1条现有弃用警告 | 供应商及情报Mock；真实本机PostgreSQL事务回滚、用户鉴权隔离SQLite |
| 前端Node测试 | 11通过 | 错误与草稿、公开引用边界、实际Vue模板新旧覆盖兼容与来源路由 |
| Vue类型检查及Vite构建 | 通过 | 保留原有约589KB首页/ECharts打包警告，无新增依赖 |
| 30题Mock行为评测 | 路由/工具/需求引用/需求报告各30/30；有引用定位15/15，无引用未造引用15/15；额外场景9/9 | 10类各3题，全部合成输入，模型和网络调用均为0 |

这些数字不是安全问题的语义准确率，不证明真实Qwen已接通，也不替代独立人工证据审查。Mock的毫秒级时延与真实系统时延无可比性。没有编造固定RAG对照提升、Token估计或价格。

已检查真实平台Key未出现在本轮Git候选文件，也未出现在公开问答响应。前端生产包检查无Key字面量、无开发授权桥；生产使用普通用户授权流程。

## 真实整链验收

2026-10-12 **13:13:19（北京时间）**完成 `scripts/verify_dynamic_fullstack.py --allow-live`，**54/54检查通过**，其中21条实际引用逐条与后端原始内容/修订及字符定位匹配。实际使用PostgreSQL/Redis/HTTP/E5/DeepSeek和官方来源，没有替换成Mock。

| 场景 | 实际结果 | 本次耗时 / 模型尝试 |
| --- | --- | --- |
| 你好 | HTTP200、真实平台基础对话，无虚假引用 | 1.641秒 / 1 |
| 已保存真实CVE | 精确查询及NVD补检，真实模型回答，4条证据；个别无对应断言的生成条目被剔除，整体partial | 4.677秒 / 2 |
| 工具审计风险与防护 | OWASP补检、真实模型生成与支持核验，4条证据，正确标注日志维度尚未支持 | 12.144秒 / 4 |
| 未上传内部资料 | insufficient_evidence、无引用，不编造内部流程 | 2.836秒 / 1次相关性判断 |
| 非标准CVE | 明确要求标准编号，0工具、0模型 | 0.048秒 / 0 |
| OSV缺包版本 | 明确澄清，0工具、0模型 | 0.050秒 / 0 |
| PyPI langchain 0.0.200 | 实际OSV查询及导入/检索，6条证据，范围与修复缺口标为partial | 19.365秒 / 4 |
| CVE-2021-44228最新评分及利用 | 实际NVD + CISA KEV；评分主体分别保留，7条证据；completed | 9.024秒 / 2 |
| 无用户凭证／无效凭证 | HTTP401，明确用户认证错误 | 无模型调用 |
| 普通用户调用内部模型配置 | 拒绝访问；不返回平台Key | 无模型调用 |

随后浏览器直接提问工具审计，已确认加载状态、真实模型最终答案、原文证据入口、实际执行记录、缺失需求与来源链接展示。授权页也验证了无效凭证提示和恢复后草稿保留。当前页面截图保存为本机忽略文件 `reports/local/fullstack/dynamic_qa_20261012.jpg`。

真实记录为本机忽略文件 `reports/local/fullstack/dynamic_live_validation.json`、`dynamic_live_captures.json`。10月11日结构验收曾通过，但有一次工具审计降级为摘录，因此保留历史记录并增强验收条件：当前该场景必须 `X-RAG-Answer-Mode=live`。本轮成功不能保证所有未来问题永远不降级。

## 联调发现并修复的问题

1. 路由函数直接改state未被LangGraph提交：增加正式 `choose_recovery` 节点，完整图测试证明外部工具确实执行、无新增停止、可进入预算内第三轮。
2. 补检成功仍显示早先“未找到”：每轮重新计算缺失项，以当前证据为准。
3. 同分同向量不同评分主体、同一KEV条目的部分转载被误判冲突：比较事实值，保留原始主体与条目，真正值差异仍不擅自裁决。
4. OWASP整页第一个h1是广告：按正文风险标题提取；三份官方资料经真实管线更新为对应风险标题，历史保留。
5. 偶发生成JSON不符合严格Draft协议：明确输出白名单与展示断言不可照抄；仅记录安全schema路径/错误类型，有预算时最多一次重新生成，始终为核验留额度。没有放松引用或硬事实规则。
6. 基础对话或已有摘录掩盖typed供应商失败：认证/限流/超时等错误在真实HTTP适配器中仍为502/503/504，并与用户401区分。

## 尚未真实验收或当前限制

- 千问：适配、能力和工作流兼容Mock通过；缺少本次可用的千问密钥，未付费真实调用。
- 用户登录：短期令牌授权可用，生产账号密码/统一身份登录、自动续期尚未实现。
- 检索：只有已注册官方目录与三个OWASP主题，不是任意联网搜索；完整厂商补丁正文抓取、完整OSV分页未实现。
- 版本：CPE布尔条件与OSV范围未全面求解，不能保证用户资产匹配或某升级版本安全。
- 回答质量：30题为实现方写的行为集，需要安全人员独立审核语义与范围；本次工具审计仍有日志维度缺口。
- 性能：OSV复合请求约19秒，接近25秒上限；尚未做并发负载、长时间运行或费用基线评测。
- 线上部署：本次验收为本机服务，没有新建可供所有同学访问的长期线上环境。
- BYOK与对外平台API Key签发按本阶段要求只保留架构边界，未开发。

## 运行命令

```bash
cd /Users/gshy/Desktop/ICT-RAG
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/run_fullstack.py status
# 全部服务停止后启动（后端需有有效平台Key）
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/run_fullstack.py start --live --detach
PYTHONPATH=packages/rag_agent/src .venv/bin/python -m pytest packages/rag_agent/tests evaluation/test_dynamic_evaluation.py -q
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/evaluate_dynamic.py --mode mock --output reports/local/dynamic_eval_mock.json
# 会产生少量实际供应商调用，仅需主动复验时执行
PYTHONPATH=packages/rag_agent/src .venv/bin/python scripts/verify_dynamic_fullstack.py --allow-live
```

前端命令在其运行副本执行 `npm run test:unit`、`npm run build`。后端测试需正确注入本机 `.secrets/fullstack.env` 中的既有数据库配置；不要把其值打印、提交或粘贴到终端历史。新机器准备与补丁顺序见 [运行说明](fullstack_local.md)，后端接入字段见 [架构及契约](dynamic_agent_architecture.md)。
