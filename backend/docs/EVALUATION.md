# 评分与评测流程

评分依据正式赛题 PDF，目标不等于实际得分。初赛监测 3/5/7 类分别是递进档，优秀需实时；富化 5+ 维度不等于优秀，还要准确与自动关联；问答语义+多轮是良好目标，跨文档深入推理是更高目标。初始目标富化准确率≥95%、多跳问答≥85%；决赛目标富化 precision/recall≥95%、问答≥95%且解释证据链，完整响应≤5s是基本档要求，不能误写成优秀门槛。AI 风险分类仅参考。

## 已实际测得

2026-10-02 17:41（北京时间）恢复服务后的本机顺序 HTTP 采样 10 次 Tool 查询：完整响应 P50 61.366ms、P95/max 98.343ms，见 output/resume-verification.json。2026-10-01 两次真实 DeepSeek 多轮响应约 1025.221ms/1191.641ms，第二次因证据不足拒绝补造；本次重启后再次确认 4 条会话消息保留。响应计时从客户端发请求到完整 HTTP 返回，未测首 token、未做并发负载或跨网络外推，两个问题不能证明准确率或全部请求≤5s。

`/ops/metrics` 包含真实来源/相关数/文档类型、当前字段证据数、事件/任务积压、最后成功时间、可观测发布到首次入库时差和缺失值。上述快照的近期到达组 25 条：P50 约 3.89h、P95 约 16.96h；历史回填、缺失/低精度时间单列。存在超过 6h 的样本，持续 SLA 尚未达标；按到达时差分组不等于独立随机 SLA 抽样。数据随启用的定时任务变化。

真实语义索引缺云端 Embedding 配置，外部 Agent 未运行，真实向量数为 0。因此没有语义检索规模增长后的有效性能成绩，不能用空库响应或测试向量代替。保留 PostgreSQL 数组精确搜索，增长后须用真实向量测试；若未来换 pgvector，需镜像/主版本核验、备份恢复演练与独立卷回退，本次未换镜像。

## 建立版本固定的评测集

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts\export-evaluation.py
& 'D:\cit\.venv\Scripts\python.exe' scripts\export-agent-cases.py
```

output/evaluation-review.json 每来源最多 10 个真实当前文档，含 document_id/hash、正文/完整性、规则预测和字段证据。审核人填写 review.reviewer、reviewed_at、ai_relevant、expected_fields。expected_fields 是字段名→规范化真实值，结构应与 predicted_fields.value 相同；不能直接复制预测作为金标。采样覆盖来源但非严格随机全量代表，不从小集声称比赛达标。

output/agent-cases.json 有 10 个真实语料问题/无结果问题、追问与版本化候选文档。候选证据不当金标答案。接通真实 Agent 后，填每条 response（完整真实接口返回）及独立审核 answer_correct/citation_correct；不填时评分为 null。不可把模型自身评价、模拟回答或通过引用存在性校验当作事实准确率。

脚本保护已有审核文件，不覆盖手工标签；重复运行会输出带时间的新文件路径，请用对应版本的路径计分。本轮规则 v4 集为 output/evaluation-review-20261001T165027524223.json（95 条），QA 候选为 output/agent-cases-20261001T165028448695.json（10 条），均待独立审核。索引/模型/provider/依赖版本、抽样时间与语料范围应与报告一起保存。

## 计分

```powershell
& 'D:\cit\.venv\Scripts\python.exe' scripts\evaluate-reviewed.py output\evaluation-review.json
& 'D:\cit\.venv\Scripts\python.exe' scripts\evaluate-reviewed.py output\agent-cases.json
```

- AI triage accuracy：审核正确文档数/审核总数，正负样本都纳入。
- 富化精确字段值：TP 为字段名+规范化值完全一致；FP 为错误/多余预测；FN 为漏出的真实字段。precision=TP/(TP+FP)，recall=TP/(TP+FN)，零分母为 null。当前粒度是整字段，不是实体/版本事件的细粒度分数；报告必须写清口径，不能与其他评测直接互换。
- QA accuracy：有真实 question/response 且独立审核的正确回答/总回答；无结果/拒答按问题金标判分，不能一律判通过。引用正确率独立报告，引用位置正确不自动意味着论点正确。
- 语料/监测召回：需要人工独立发现的应采集记录分母、固定来源与时间窗，当前字段 recall 不等于互联网或全语料召回。

当前没有独立审核标签，准确率/召回率为空，这是待评测结果。提供审核人/时间只是输入记录，还需团队保证其独立性和证据质量。

## 后续联合验收

按 V1 协议完成真实外部 Agent/index verify，再跑 document→event→index→语义检索→引用答案→追问；记录重复、更新/失效、重放、失败、隔离、无结果和持久化。建立≥7类持续观察期，记录失败源、历史回填与遗漏，再报告达标比例和 P95。不得将目前的关键词问答、离线样本或模拟合同测试记为真实 RAG/高分成绩。
# 2026-10-09 问答评测补充

新增36问题/12文档虚构评测，24开发、12保留验收，标注为AI草稿而非独立人工金标准。完整定义和实测结果见 `D:\cit\ICT-team\docs\qa_acceptance_20261009.md`。固定基线提交9daad1c2a93ac5dfcd4f699860602f9a36320611，记录代码/语料SHA256、E5不可变revision、K和分块参数。语义阈值没有按保留组调参。

P@3现在使用固定分母3；返回结果相关比例另命名precision_of_returned，不能与2026-10-08曾误名P@3的历史数字混用。Recall按草稿相关文档集合计算，不代表全库召回。回答切题程度、语义引用支持评分保留null待独立人工复核；来源真实、引用位置有效和模型自检不能替代该评审。

离线评测0次LLM调用；本轮真实入口九组修复后请求与两个英文基线，合计27次模型HTTP尝试。延迟逐请求记录，少量样本不报告P95或SLA通过。48小时观察仍进行且已有缺口，见运行手册。
