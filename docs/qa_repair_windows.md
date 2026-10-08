# Windows 追溯问答修复记录（2026-10-08）

本轮修改已在本机部署，入口 http://127.0.0.1:5173/qa；后端为 `D:\cit\后端开发`，Agent 为 `D:\cit\ICT-team\packages\rag_agent`。当前 Agent 为真实 DeepSeek 生成模式，真实缓存 E5 向量模型在启动时预热。代码已整理到 feature/agent-qa-relevance 分支，供 Agent 队友审查。

## 根因及修改

1. 原混合检索接受任意词语重合，中文通用词把法规推到前列。新增通用词过滤、领域词的中英文扩展、复合主题筛选以及每文档最多两个候选片段。保留原有 0.78 语义候选阈值，没有根据验收样本调整该阈值。
2. 原流程找到片段就生成。真实生成模式增加生成前的证据相关性检查，以及生成后的结论支持检查；检查只能选择已有证据或结论索引，不能扩大范围。证据不足、检查失败或结论全部撤回时明确返回证据不足。模型核对仍可能判断错误，不等于独立人工审核。
3. “怎么修复？”原先不识别为追问，连续追问也只看最后一句。现在扫描受限的用户历史，延续最近明确对象；新的实质话题阻断旧对象，歧义要求澄清。助手历史不能作为事实来源。适配器保留后端传来的最多八条消息。
4. 原答案机械追加各字段，且把 1200 token 当成 1200 UTF-8 字节。现在只在正文追加被问到的字段，排除未被答案引用文档的结构化断言；模型生成仍有 token 限制，传输使用独立字节保护并保留完整行，避免半句和破损引用。字节保护不是精确的供应商 token 测量。
5. 后端新增并保存 `generation_mode`、`llm_http_calls`，调用详情可查看；幂等缓存保留这些字段及响应头。旧调用缺少字段时返回 null，旧历史不会被补写成真实生成。
6. 首轮部署曾因缓存向量模型首次加载超时；已将预热移入 Agent 启动生命周期，启动就绪后才接收问答，补测和最终整轮均成功。

## 修复前后实测

| 问题 | 修复前 | 修复后 |
|---|---|---|
| 访问控制相关情报有哪些？ | 主要回答法规、个人信息保护和备案 | 返回 MetaPermit、ToolFence、能力令牌及智能体授权架构等相关资料，原文支持已抽查 |
| 智能体工具审计有什么风险？ | 混入法规及不相关 vLLM 输入校验漏洞 | 回答评估失真、审计追踪缺失和工具调用攻击链重建困难，三篇对应摘要支持已核对 |
| 指定 CVE-2026-73558 | 能定位但重复追加字段、容易截断 | 保留目标，简洁说明风险和来源评分 |
| 它影响哪些版本？→怎么修复？ | 第二次简短追问丢失对象，回答法规处置措施 | 连续保持同一 CVE，回答影响版本和修复版本；未解析 CPE 适用性仍标为未知 |
| 随后切换到数据投毒 | 混入法规内容 | 新检索数据投毒资料，没有带入上一漏洞 |
| 不存在的 CVE | 证据不足 | 保持证据不足，无来源、无模型调用 |

最终通过 Vite 5173 → 后端 `/api/v1/agent/query` → Agent `/v1/query` 的七组请求全部返回：五组 completed、一组 partial、一组 insufficient_evidence。六组真实 DeepSeek 生成，每组 2—3 次 HTTP 调用；不存在的编号调用为 0。最终有证据请求耗时 2.266—5.406 秒，缺失编号 0.204 秒。这只是本轮样本延迟，不是长期 SLA。

## 测试与评测边界

- Agent 回归 **86/86**，后端 **70/70**。包括引用、硬过滤、提示注入、版本与作用域、失败处理，以及新增多轮、中文主题、证据选择和结论撤回测试。测试替身不计为真实 DeepSeek。
- 运行检查 **15/15**：七组调用状态保存、会话顺序、同一目标追问、话题切换、无证据处理、排除错误法规来源、后端与 Agent 就绪。
- 冻结的虚构小语料：六文档，开发六问题、保留验收六问题。真实缓存 E5，无模型生成；独立索引 `reports/local/windows-fullstack/qa-eval-synthetic.db`，不进入生产知识库。
- 五个有答案开发问题的平均 Precision@3：53.3% → 100%，Recall@3：100% → 100%；首位命中率：20% → 100%。开发无答案问题正确返回空。
- 五个有答案保留验收问题的平均 Precision@3：60% → 60%，Recall@3：100% → 100%，首位命中率：100% → 100%；无答案编号正确为空。英文检索仍有多余候选，由生成前相关性检查继续筛选。这些数字不能代表全库准确率、召回率或比赛达标。
- 评测初版发现虚构编号缺少结构化标识及 GHSA 字符集错误，修正样本标注后重跑。没有按保留问题调整检索阈值。
- 容器 API/PostgreSQL/Redis 健康，worker 和唯一 beat 运行；迁移检查没有新操作，未改变数据库结构。生产真实文档 1072，评测来源进入生产数量 0。已有索引和数据库卷保留，部署前备份：`D:\cit\后端开发\output\pre-timeliness-20261008T141406Z.dump`。

本地完整请求/证据与原始失败尝试均保留在 `reports/local/windows-fullstack/qa-relevance-*.json`；运行结果 `qa-runtime-validation.json`，小样本结果 `qa-relevance-evaluation.json`。这些验证输出按现有规则留在本地。

## 使用与交接

建议在页面新建会话，旧答案保持原样。先问明确主题或编号，再追问；证据不足时补充产品、编号或来源。API 文档 http://127.0.0.1:8010/docs。

```powershell
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py status
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py restart-agent --mode live
# 整个本机三模块恢复：先 stop，再 start。Docker 数据与后台任务保留。
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py stop
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py start --no-build --mode live
Get-Content D:\cit\ICT-team\reports\local\windows-fullstack\logs\agent.log -Tail 50
```

复验会产生有限的真实模型调用费用：

```powershell
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\verify_qa_relevance_windows.py after
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\validate_qa_runtime_windows.py
cd D:\cit\ICT-team
$env:PYTHONPATH='D:\cit\ICT-team\packages\rag_agent\src'
& D:\cit\ICT-team\.venv\Scripts\python.exe -m pytest packages/rag_agent/tests -q
cd D:\cit\后端开发
& D:\cit\.venv\Scripts\python.exe -m pytest -q
```

交给 Agent 队友审查：`agent/nodes.py`、`agent/tools.py`、`retrieval/hybrid.py`、`providers/llm.py`、`schemas.py`、`integration/api.py`、新增回归和小样本。后端配套：`backend/schemas/agent.py`、`backend/api/v1/agent.py`、对应测试及 OpenAPI。启动脚本增加仅重启登记 Agent 的命令。

剩余限制：检索词表和小语料覆盖有限；证据选择及支持核对使用同一个模型，仍需扩充独立标注与人工复核；八条历史以外的对象可能需要用户重新指定；部分来源仅有摘要，无法回答摘要以外的细节；比较问题耗尽调用预算时明确披露结论核对不可用。历史回答不自动重新生成。本轮未处理 GitHub。
