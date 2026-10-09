# 富化质量监测、人工审核与评测

当前实现新增0008增量迁移，保留原文、富化值、既有审核文件和数据库卷。默认范围是非演示、规则判断AI相关的当前漏洞文档；可选择公告或全部文档、某来源，以及source_reported/external/all预测。多种方法合并时相同事实去重，不重复计分。

## 团队副本使用

进入团队仓库的 `backend/`（本工程根目录），按 README 初始化本人的 `.env` 并启动 Compose。使用 `uv run python scripts/enrichment-quality.py summary` 查询，其余 create/export/submit/report 子命令同下文。下文绝对 Windows 路径、真实审核批次、观察 ID 和验收输出仅属于原开发机器；团队新库需创建自己的审核批次。`verify-enrichment-quality.py` 和部署脚本为原机器复验工具，不能直接用于新机器。

## 如何查看

在VS Code PowerShell执行：

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py summary
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py issues
```

脚本仅在内存读取本地管理员配置，控制台不会显示凭据。结果写入忽略目录`output/enrichment-quality/`，不能提交GitHub。Swagger为 http://127.0.0.1:8010/docs ，展开“富化质量审核”；管理密钥只由管理员本地使用，不交给前端。

接口前缀`/api/v1/ops/enrichment-quality`，全部要求X-API-Key：

| 方法/路径 | 含义 |
|---|---|
| GET 空路径 | 实时字段覆盖、自动问题、最近同范围批次的当前版本人工评分 |
| GET /issues | 问题分页；支持severity/code筛选；不访问外部链接 |
| POST /batches | 冻结分来源样本，201；默认每来源12条、最多100条 |
| GET /batches | 分页查询批次，默认排除演示 |
| GET /batches/{id} | 导出原文、来源、版本、预测与空白审核模板 |
| POST /batches/{id}/reviews | 追加一份文档审核；相同请求幂等；修改已保存标签409 |
| GET /batches/{id}/report | 分维度TP/FP/FN和审核覆盖率；默认仅计当前有效预测 |

实时扫描最多1000条，批次候选最多5000条，响应明确报告扫描是否完整。超过范围时应分来源评估，不能把部分扫描当全库。覆盖率分母是本次所选文档，不表示该字段对每种文档都适用；因此建议漏洞、公告分别观察。

## 自动检查的边界

六维：affected、cwe、cvss、remediation、analysis、poc。检查来源值/定位/原文摘录的一致性、字段结构、CWE编号格式、CVSS数值范围与版本前缀、CPE命名、版本边界、OSV事件结构、链接URL及外部证据是否存在。CVSS原来源只有向量或只有分数时分别警告，未知不会填为零。

自动检查不计算CVSS向量对应分值、不判断复杂资产适用性，不验证链接可访问或语义支持，不执行POC。source_reported原文一致不等于事实正确；external已由原写入协议校验引用，本监测只做结构和证据存在性检查，不能冒充再次核验所有外部引用。警告次数按字段条目计算，不是错误文档比例。

已有唯一beat在活动观察窗口内每分钟保存`detail.enrichment_quality`，记录覆盖和结构问题。窗口结束后自动采样随该窗口结束；实时接口继续可查。没有增加第二个beat，也不保证主机关机期间采样。

## 建立和填写审核材料

```powershell
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py create --per-source 24
# 记录返回batch_id，再导出；运行验收脚本已生成的批次可以直接复用。
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py export --batch-id <batch_id>
```

每个case冻结content_hash、prediction_hash、原文raw_payload、原字段证据与规范化predicted_units。review_template的expected默认null，不能直接提交。人工应先读原文与source_fields，核对语义，再填写，不能直接复制预测冒充独立金标。

复制某个case的review_template到一个本地JSON文件，填写reviewer、annotation_origin、independent及field_reviews。真正由人独立审核才填写human和true；AI辅助标签填写ai_assisted，保留但不进入独立人工评分。只审核一两个字段时，删除未审核字段；不知道金标就不提交该字段。expected=[]表示经过核对确认该字段没有相关事实，和未审核完全不同。

例：某原文明示CWE-502，则该字段为：

```json
{"expected": ["CWE-502"], "basis": "在当前冻结原文source_fields.cwe.ids核对，仅明确报告CWE-502；已检查其他原文字段。"}
```

该例是填写格式，不是任何真实文档的审核标签。basis记录原文位置和判断理由，至少10字符。名称由审核者填写，系统不能仅凭声明验证审核者是否真正独立，团队应保留审核责任与记录。

```powershell
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py submit --batch-id <batch_id> --file 'output\enrichment-quality\我的审核.json'
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py report --batch-id <batch_id>
```

全部完成前显示partially_reviewed；未有独立人工标签时显示awaiting_independent_review和null分数。审核记录不覆盖：发现标签错误时新建批次重新审核，不能改写旧证据。

## 计分口径与版本保护

- CWE按编号；修复/分析/POC按URL；CVSS按版本+向量+分数；affected按每个CPE及边界，或OSV包及其完整范围/版本集合。这些是字段事实单元，不是每个版本拆成一条以放大成绩。
- TP为预测与审核事实的交集，FP为多余/错误预测，FN为漏出事实；precision=TP/(TP+FP)，recall=TP/(TP+FN)。重复事实去重，JSON键顺序不影响结果，9与9.0相同，True与1不同。
- exact_field_accuracy另计整字段集合完全一致率，包含已审核负例；报告同时保留负例数。不能与precision混称。
- 缺失字段标签不计分、不视作负例；零分母为null，未知预测遇到明确正例计FN。分维度与整体均报告样本数及审核覆盖率。
- 文档更新、失效、相关性改变或富化预测变化后，旧审核不进入当前评分；历史批次可用current_only=false复核，不当作当前成绩。
- 这是抽样字段事实召回，不等于“应采漏洞覆盖率”。后者需独立发现的应采漏洞分母；vulnerability_coverage_recall保持null，competition_threshold_verified始终false，需要团队另行验收。
- 现有V1 export-evaluation/evaluate-reviewed脚本和95条旧审核样本保留；它们用整字段粒度，不能直接与本V1质量事实粒度数字混用。

## 验证与部署

`scripts/test-quality-isolated.py`创建新的verify_quality_*数据库，完整执行迁移和事务回滚测试，保留验证库以检查；不操作生产文档。`scripts/verify-enrichment-quality.py`验证真实运行接口、鉴权、空标签、运行源码与唯一beat，并生成真实冻结样本；再次执行用--batch-id复用已有样本。以上均不调用大模型。

部署使用原`scripts/deploy-timeliness.py`，先备份并校验dump、保留旧镜像，再使用已有Agent overlay构建、增量迁移、重启API/worker/唯一beat，不删除卷。旧镜像与0008迁移头不匹配时既有回退保护会拒绝直接回退，不能删除迁移记录以绕过。

## 2026-10-09 实际验收

- 独立验证库`verify_quality_20261009t105229z_26c18748`从零执行0001—0008和Alembic check，完整回归102/102；含新增15项质量回归。最终相关修改后再次运行质量15/15通过。已有FastAPI测试客户端依赖有弃用提示，不影响本轮结果。
- 运行接口、权限、真实样本、空标签不计分、源码哈希、唯一beat和自动持久化共15/15通过；生产Alembic check无新操作，Compose解析通过，API/PostgreSQL/Redis健康。
- 实际扫描207条非演示AI相关当前漏洞文档：affected187、CWE119、CVSS194、修复63、分析168、POC元数据10条有源字段。上述是覆盖数量，未知不等于错误或必须存在该信息。
- 自动结构/原文一致性错误0；警告281条：CVSS分数未知218、向量未知63。警告按向量条目计，可在同一文档重复，不代表281篇错误文档，也不构成独立人工准确率。
- 已冻结48条真实样本，OSV/NVD各24条；批次`e7a6d1e9-221a-450b-b6af-4a70bc97c4be`。审核材料为`output/enrichment-quality/real-review-20261009T105945957008Z.json`，运行记录为同目录`runtime-20261009T105945957008Z.json`。原85条/95条旧样本仍保留。
- 真实独立人工审核0条；precision/recall/exact_field_accuracy为null，未声称比赛≥95%。已保存1个真实审核批次、0条人工审核；生产中quality-fictional测试来源0条，大模型调用0次。
- 部署前备份`output/pre-timeliness-20261009T105818Z.dump`并校验归档；原卷保留，0008只新增质量表。活动48小时观察中已真实保存质量快照，既有观察缺口保留，不能把本轮部署称为连续稳定通过。

复查现有样本，无需新建批次：

```powershell
& 'D:\cit\.venv\Scripts\python.exe' scripts\enrichment-quality.py report --batch-id e7a6d1e9-221a-450b-b6af-4a70bc97c4be
& 'D:\cit\.venv\Scripts\python.exe' scripts\verify-enrichment-quality.py --batch-id e7a6d1e9-221a-450b-b6af-4a70bc97c4be
```
