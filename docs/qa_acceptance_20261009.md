# 问答评测与联调记录：2026-10-09

代码继续使用 `D:\cit\ICT-team\packages\rag_agent`，后端为 `D:\cit\后端开发`。本次只修改 Agent 检索及必要配套脚本，模型仍为原 DeepSeek，真实向量仍为缓存的 multilingual-e5-small。未处理 GitHub。

## 本次修改

- 英文和中文使用相同主题约束；工具审计不再仅凭 `tool call` 一词接纳资料。语义阈值仍为 0.78，没有按保留验收问题调阈值。
- 原评测脚本改用固定提交 `9daad1c2a93ac5dfcd4f699860602f9a36320611`，记录代码和语料哈希；新脚本另保存当前检索、节点、模型配置哈希和模型不可变 revision。
- 旧记录名为 Precision@3 的数字实际以返回数量为分母。现在明确分为固定分母 3 的 P@3 和返回结果相关比例 `precision_of_returned`，旧记录不能直接混用。
- 新增 `qa_acceptance_v1.json`：12 篇虚构材料、36 个问题，24 开发/12 保留验收。标注为 AI 编写、未经独立人工审阅；原文引句、相关文档、预期行为随结果保存，不能称为人工金标准。
- 只读运行验证可有限重试 GET，问答 POST 不自动重试；模型调用不会因验证超时被重复发送。保留历史验证报告。

## 离线结果

真实缓存 E5、独立 SQLite 验证索引、0 次模型调用。固定代码与语料元数据见 `reports/local/windows-fullstack/qa-acceptance-20261009T063257Z.json`。

| 分组（仅检索问题） | 指标 | 固定旧基线 | 本次代码 |
|---|---|---:|---:|
| 开发：13 正例/3 负例 | P@3（固定分母3） | 30.77% | 33.33% |
| 同上 | 返回结果相关比例 | 48.72% | 91.03% |
| 同上 | Recall@3 | 92.31% | 100% |
| 同上 | 首位命中率 | 61.54% | 100% |
| 同上 | 负例正确为空 | 2/3 | 3/3 |
| 保留：7 正例/1 负例 | P@3（固定分母3） | 38.10% | 38.10% |
| 同上 | 返回结果相关比例 | 45.24% | 85.71% |
| 同上 | Recall@3、首位命中率 | 100%、100% | 100%、100% |
| 同上 | 负例正确为空 | 1/1 | 1/1 |

另 12 个多轮/歧义/无证据/部分证据/注入/作用域行为检查全部通过。P@3 对只有一个相关文档的问题最高为 1/3，不能误读为只有三分之一答案正确。行为检查与回答语义质量不同；人工回答切题、引用支持评分保留 null，等待独立复核。检索耗时逐题记录，未报告小样本 P95。

## 真实模型验证

今日修改前的两个英文问题分别保存于 `qa-relevance-before-access_en.json`、`qa-relevance-before-audit_en.json`。重启 Agent 后，通过前端 5173 → 后端 `/api/v1/agent/query` → Agent `/v1/query` 完成九组问题。七组 completed、一组 partial、一组 insufficient_evidence；八组使用真实 DeepSeek，合计 21 次 HTTP 尝试，缺失编号 0 次。加上今日两个基线的 6 次，本轮总共 27 次；单次最多3次。没有把昨天的结果当作今日实测。

有证据请求 2.546—5.156 秒；无证据编号 0.203 秒。真实服务延迟是有限样本，不是长期 SLA。完整答案、来源版本和引用保留于本地 `qa-relevance-after.json`。调用保存、多轮顺序、对象保持、话题切换和就绪等 **17/17** 检查通过。首次只读持久化查询曾超时，复验通过；没有因此重复付费问答。

| 诊断内容 | 今日前/后对比与边界 |
|---|---|
| 英文访问控制 | 修改前包含 DualView 等较宽泛 IPI 内容；修改后集中于授权架构、MetaPermit 与 Ajar 开放特权。并非前一版本完全无关。 |
| 英文工具审计 | 前后都能找到审计资料，修改后候选集中到审计追踪和可信智能体综述；两次答案均有对应摘要，不能仅据此声称整体质量提高。 |
| 中文工具审计 | 引用了评估失真研究，也讨论记忆审计的局限；后一部分对“工具审计”主题较间接，仍需人工判断是否应更严格筛选。 |
| CVE → 影响版本 → 修复 | 保持同一 CVE；影响版本回答为 partial，保留 CPE 布尔适用性未解析提示；修复版本有来源依据。 |
| 切换数据投毒 | 不带入上一 CVE；引用投毒及训练数据资料。 |
| 不存在编号 | 无引用、明确证据不足、没有模型调用。 |

本次由编码助手核对了典型答案与已保存公开摘要，关键数字和版本可以对应原文；这属于 AI 辅助核对，不是独立人工评分。模型支持检查也使用同一 DeepSeek，仍可能漏掉概括或主题偏移。输出的“已接通”与“质量达标”不能混为一谈。

## 复验与审查

```powershell
cd D:\cit\ICT-team
$env:PYTHONPATH='D:\cit\ICT-team\packages\rag_agent\src'
& D:\cit\ICT-team\.venv\Scripts\python.exe scripts\evaluate_qa_acceptance.py
# 可单独 --split development；验收组只在冻结实现后运行，不据此调参。
& D:\cit\ICT-team\.venv\Scripts\python.exe -m pytest packages\rag_agent\tests -q -p no:cacheprovider --basetemp ('D:\cit\output\pytest-agent-' + [guid]::NewGuid().ToString('N'))
& D:\cit\.venv\Scripts\python.exe scripts\validate_qa_runtime_windows.py
```

Agent 最终回归 **91/91**（英文检索改进后89项，加2项Windows回退保护）。默认Temp目录本轮权限不足，改用新的项目内临时目录后通过，未重新安装依赖。旧回退镜像的迁移头与当前0007不匹配，实际只读检查正确拒绝回退，未停止服务或改变数据。

再次运行 `verify_qa_relevance_windows.py after` 会产生真实模型费用，并新增九组验证记录；没有必要时不重复调用。独立人工审核请先检查报告内 `gold_evidence`、`result.sources` 和 `review` 空字段。

Agent 队友审查本次新增/修改：`retrieval/hybrid.py`、`tests/test_qa_relevance.py`、两个评测 fixture、`scripts/evaluate_qa_acceptance.py`、`scripts/evaluate_qa_relevance.py`、`scripts/validate_qa_runtime_windows.py`、`scripts/verify_qa_relevance_windows.py`、`scripts/verify_index_recovery.py`。前次节点/提示词修改仍保留，未覆盖队友代码。
