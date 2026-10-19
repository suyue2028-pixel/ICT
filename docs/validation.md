# 本次实际验证记录

日期：2026-10-01T12:59:04.960289+00:00。工作目录 `/Users/gshy/Desktop/ICT-RAG`，分支 `agent-dev`。交付保存在本地，未推送或部署外部服务。

## 环境与安装

Apple M5、16 GiB 内存、macOS 26.5.1 ARM64、Python 3.11.16，CPU 推理。`requirements.lock` 固定依赖；安装的83个包兼容检查通过。非 editable 包已重新安装，去掉 PYTHONPATH 后已运行 CLI 多轮与比较示例。

Embedding 为真实 `intfloat/multilingual-e5-small`、384维、revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`。模型下载是显式准备步骤，查询只读本地缓存。远程模型缺密钥，实际调用次数为0；当前生成方式为 extractive。

## 结果

| 检查 | 实测结果 | 解释 |
|---|---|---|
| 合成回归 | 54 passed，0 failed/error/skipped | fictional CVE-2099 + 明确 mock；覆盖预算终止、取消、版本事务、引用和过滤边界 |
| 真实资料 | 19篇，4文档类型、4采样渠道，97正文块 | NVD、OWASP、arXiv、欧盟；标准类型含行业指导，不等于正式规范标准 |
| 真实资料自动检查 | 25/25 | 参考为实现方预置来源片段，人工复核待完成 |
| Recall@5 | 0.9649，19个可评分问题 | 从固定片段映射正文证据；排除无证据/澄清问题 |
| 引用可定位率 | 100%，75条来源引用 | 只验证原文范围/JSON pointer/URL，不代表语义支持已证明 |
| CVSS字段准确率/覆盖率 | 100%/100%，7个预声明参考槽 | 只评价声明的CVSS版本/分数；不是全领域问答准确率 |
| Cold / warm | 3.805s / 13.70ms | 首次请求含模型加载；模型已在磁盘 |
| 端到端 p50 / p95 | 3.79ms / 27.67ms | 25请求含首条cold，0 LLM；不能外推 live 生成或并发吞吐 |
| 本机真实HTTP联调 | 13/13 | health、exact/hybrid/compare、证据详情/404、非法时区422、局部缺失等 |
| 重复导入 | 19 unchanged，0 failed；generation 20→20 | 13条关系保留，0新增 |
| 安装后CLI | 多轮和跨文档比较通过 | 来源摘录、真实索引指纹、LLM调用0，修复版本缺失返回partial |

LangGraph 预算耗尽统一返回 error，不能被普通 provider 降级异常吞掉；异常路径也保留已完成改写次数。回归包含 max_llm_calls=0 的精确/比较查询和1次调用预算的比较查询。

## 未完成的外部验证

没有 RAG_LLM_KEY，所以 DeepSeek 适配仅经过 HTTP 测试替身验证，没有声称远程生成成功。人工引用支持率、跨文档推理正确率为 null，`reports/human_review.json` 待安全/后端同学填写；本次没有满足人工金标准签字条件。

NIST采样页面失败明细保留在 manifest。复杂CPE适用性求值、真实生成、正式标准扩充与人工复核仍需继续。后端负责采集/定时更新、日志和会话、公开业务API；本次只交付模块接口和演示层。

## 重跑和证据

```bash
cd /Users/gshy/Desktop/ICT-RAG
source .venv/bin/activate
export PYTHONPATH="$PWD/packages/rag_agent/src"
export HF_HOME="$PWD/.cache/huggingface"
export HF_HUB_OFFLINE=1
python -m pytest packages/rag_agent/tests -q --junitxml=reports/regression.xml
python -m rag_agent.evaluation.run --cases data/evaluation/acceptance.json --output reports/evaluation.json
```

结构汇总 `reports/validation_summary.json` 记录硬件、各项报告路径、指标口径和代码SHA-256。回归结果 `reports/regression.xml`；真实检索结果 `reports/evaluation.json`；HTTP、幂等与安装后CLI结果分别为 `http_validation.json`、`idempotence.json`、`demo_examples.json`。重新运行评测会生成新的结果和空白人工工作表，已有人工评分应另存归档。

接入契约见 `docs/contracts.md`，职责和后续事项见 `docs/handoff.md`，参考源码 commit、来源与发现见 `docs/reference_review.md`。
