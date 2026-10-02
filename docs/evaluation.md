# 验证口径与人工评分工作表

本次区分三层证据：tests 中的合成回归、真实 E5 CPU 检索/来源摘录、远程生成与人工语义评测。报告不能跨层解释。

## 自动检查

`packages/rag_agent/tests` 使用 fictional CVE-2099、明确 synthetic 标记和 MockEmbedding/MockLLM。覆盖编号不替代、硬过滤、不存在字段、幂等/版本更新、失败保留旧版、模型指纹、float32、完整 CPE/v4、来源冲突、伪引用、资料指令不执行、预算与取消、多轮歧义、缓存失效、API schema 和 provider 请求预算。

`data/evaluation/acceptance.json` 固定25个真实资料问题及参考片段/字段。`tuning.json` 独立，未用于验收指标。acceptance 在首次执行前写定，结果文件保留 cases_sha256；没有根据首次结果修改预期答案。参考由本次实现方根据源片段定义，尚未经过人类评审；这不是已经完成的人工金标准。

Recall@5：每问题预先定义外部文档 ID 和文本片段；映射到当前版本中包含该片段的正文 evidence 集，取实际检索前5个 evidence 的集合召回。无证据/澄清/硬过滤排除问题不参与 Recall 分母；参考片段不可获取时单独报告。自动 checks 包括期望状态、参考可用、文档覆盖、缺失字段、引用定位和预算；通过检查不等于语义正确。

字段准确率：只对 acceptance 中明确列出的 CVSS 版本/分数参考槽评分，正确字段记录/已回答字段记录；另报已覆盖参考槽/应答槽。不会把“跟自己存储一致”当成全领域事实准确率，未评分的影响范围、自然语言结论、来源本身真实性不包含在这项数字中。coverage 的分母和 answered count 都写入结果。

引用可定位率：正文 excerpt 必须等于源 text[start:end]，结构 excerpt 的 JSON 必须等于输入 payload 的 pointer 所指对象；文档当前版本和 URL 也匹配。只验证来源定位，不验证句子蕴含关系。

时延：cold_first_query_ms 包含此进程首次模型加载（模型已经下载到磁盘），warm_same_query_ms 重复第一个问题；p50/p95 为25个请求的端到端样本分位数，包括第一条冷请求，不剔除慢样本。额外记录依赖版本、CPU、系统/Python、模型 revision、document/chunk 数、索引 generation、调用数。这里 LLM 调用为0，不能外推到 live 时延或并发服务吞吐。

## 人工评审必须继续

`reports/human_review.json` 每问题包含 claims、来源与空白评分。让安全/后端同学分别填写 reviewer、notes、citation_support，再由第二人复核。

引用支持 rubric：逐句检查每条引用是否直接支持 claim 的对象、范围、时间、因果和数字；1=直接支持且没有扩大，0=不支持/矛盾，null=未审。推断需单独标 inference，并检查是否诚实表达不确定性。对恶意材料原文的摘录只证明该资料包含此内容，不能把资料中的指令作为行动指令。

跨文档 rubric（0-3）：0=错对象或无依据结论；1=找齐两份资料但仅并列；2=有证据的比较并保留缺失/冲突；3=每步关联都有来源、确认/候选明确且结论正确。至少2分才算该问题比较正确；不能把相似资料并列称为多跳攻击链。当前 extractive 模式只做证据对照，推理正确率为 null，待 live 和人工评审。

无证据 rubric：缺失目标不替代、不放宽硬过滤、不编修复版本；局部有证据须 partial 并列出缺项。报告自动行为检查和人工语义检查各自的分母。

`reports/evaluation.json` 为可重跑实测结果。演示集小、资料时点固定，所有指标均不代表普遍泛化能力或比赛指标已达标。
