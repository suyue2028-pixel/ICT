# 动态 Agent 小型评测

## 数据与口径

`evaluation/dynamic_questions.json` 包含 30 个问题，每类 3 个：问候、问候混合 CVE、无效 CVE、历史指代、安全概念、工具审计、CVE 多需求、缺少证据、来源冲突、来源故障。

这是实现方编写的行为与契约检查集，尚未由独立安全人员审核。期望路由、工具和需求标识来自项目现有接口约定；不存在由模型生成的事实标准答案。

Mock 使用已经存在的 `packages/rag_agent/tests/fixtures/documents.json`。其 CVE-2099 编号、分数、修复版本和冲突都明确标记为 **synthetic**，只能验证行为，不能当作真实安全情报。真实后端变体仅使用 `data/evaluation/acceptance.json` 已保存的真实编号及来源标识，不重新编造漏洞事实，也不宣称这些来源仍是最新信息。

## 默认：只检查数据集

```bash
.venv/bin/python scripts/evaluate_dynamic.py
.venv/bin/python -m unittest discover -s evaluation -p 'test_*.py'
PYTHONPATH=packages/rag_agent/src .venv/bin/python -m pytest \
  packages/rag_agent/tests/test_recovery_graph.py -q
```

默认模式 `validate_dataset` 不发送网络请求，不调用模型，不读取平台模型环境配置。会检查题数、类别、synthetic 标记、实际工具白名单及固定来源文件路径。

## 独立 Mock 回归

```bash
.venv/bin/python scripts/evaluate_dynamic.py \
  --mode mock \
  --output reports/local/dynamic_eval_mock.json
```

此模式在临时目录新建 SQLite 数据库，使用 `MockEmbedding` 和来源摘录模式，LLM 调用预算为 0。官方情报工具由明确的空结果／故障测试替身代替，不发送真实 NVD、CISA、OSV 或 OWASP 请求。运行结束自动清理临时库，不修改现有真实数据库、向量库或知识资料。

Mock 的关键词候选、来源定位和执行记录不代表真实语义检索或大模型回答质量。部分概念问题只能返回候选摘录，需求是否真的得到支持仍须人工审查。

## 已认证后端模式

```bash
# 将普通用户令牌安全设置到 ICT_EVAL_USER_TOKEN；不要把令牌写进命令历史。
.venv/bin/python scripts/evaluate_dynamic.py \
  --mode backend_http \
  --allow-backend-calls \
  --backend-url http://127.0.0.1:8010/api/v1 \
  --limit 3 \
  --output reports/local/dynamic_eval_backend.json
```

脚本从指定环境变量读取普通用户令牌，先通过真实 `/auth/me` 验证，随后调用现有 `/agent/query`。**此模式可能调用平台付费模型和真实情报 API**，必须显式加 `--allow-backend-calls`。本次开发只运行数据检查与 Mock，未执行付费后端评测。

令牌不写入结果。客户端禁用代理和重定向，服务地址仅允许 HTTPS 或 loopback HTTP，不接受 URL 内嵌凭据。脚本不签发令牌，不上传合成资料，不绕过鉴权。

需要真实历史上下文的题目通过正常用户问题建立会话。合成来源冲突、来源故障注入、伪造 assistant 历史及缺失合成编号等无法在当前真实后端安全复现的题目会跳过并说明原因；不会把跳过计作通过。

## 输出指标

| 输出 | 实际检查内容 | 不能据此宣称 |
| --- | --- | --- |
| 路由契约检查 | 返回类型是否符合预期；基础对话单独识别 | 任意问题的分类准确率 |
| 工具选择契约检查 | 实际返回的工具属于注册白名单，并调用题目要求的工具 | 工具取得的事实一定正确 |
| 引用映射与定位检查 | 引用对应实际返回来源；Mock 对照存储与字符／JSON pointer，后端对照原始版本与 locator | 该证据语义上支持每条结论 |
| 需求引用契约检查 | 需求状态合法，引用存在，非缺失状态必须有实际证据编号 | 需求已经被事实完整回答 |
| 需求报告完整性 | 题目期望的需求标识是否全部报告 | 需求支持准确率 |
| 平均响应时间、工具数 | 当前模式、本数据集实际运行样本 | 生产吞吐、并发 SLA |
| Token 用量 | 仅记录 API／运行摘要明确返回的计数 | 从字数估计真实消耗 |

费用 `cost` 与语义证据支持准确率保留为 `null`。人工评审状态明确为 `manual_not_evaluated`。缺少用量字段时 `token_usage` 为 `null`，不填假 0。

引用定位指标的分母只包含实际返回证据的回答；没有引用的回答单独检查未伪造引用。合成冲突、字段缺失和来源故障披露等额外场景单独汇总，不能用其余结构项通过掩盖故障场景失败。出现契约失败时脚本写出完整结果并以退出码 1 结束。

需要补充人工评测时，逐项检查实体、时间、产品与版本范围、数字、因果、修复适用性、冲突和引用语义，再由第二人复核。结构检查通过不等于事实正确。

## 现有固定 RAG 基准

原来的 `data/evaluation/acceptance.json`、`reports/evaluation.json` 和 `docs/evaluation.md` 保留。

```bash
.venv/bin/python scripts/evaluate_dynamic.py --mode mock \
  --fixed-rag-report reports/evaluation.json \
  --output reports/local/dynamic_eval_with_baseline_reference.json
```

这个选项只记录既有报告的哈希和可用指标名称。两套题目、模型模式、资料时点没有配对，因此不计算“动态 Agent 比固定 RAG 提升多少”，也不生成比较结果。需要同一数据集、相同资料快照和模型预算的配对实测后才能比较。

## 本轮实际运行

本轮完成默认数据检查、7 个评测脚本单元测试、3 个完整 LangGraph 恢复回归，以及全部 30 题 Mock。完整图回归覆盖外部故障被实际调用并披露、空补检及时停止、新证据进入后允许预算内第三轮。

首次 Mock 暴露了路由函数修改状态未写回的问题：外部故障测试替身未执行，初始精确检索却重复执行。修复为正式恢复决策节点写回状态后重新运行，路由与工具结构检查均为 30／30，实际有引用的 15 个回答定位检查为 15／15，无引用的 15 个回答未伪造引用，额外合成场景为 9／9。

这些数值仅表示这套合成资料下的契约与恢复回归通过；语义支持、实际来源质量、真实模型表现和真实服务时延仍未由本脚本评估。详细运行结果保存在本机忽略目录 `reports/local/dynamic_eval_mock.json`，不能当作真实 API 评测。
