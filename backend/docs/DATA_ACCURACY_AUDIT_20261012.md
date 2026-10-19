# 数据准确性检测记录（2026-10-12）

结论：本轮未发现版本完整性、原生字段复制或已核算CVSS基础分数的错误；但发现AI安全主题分类存在明显误收。自动结构校验通过，不代表所有情报都准确，也不能据此报告比赛要求的独立人工准确率/召回率。

## 实际检测范围

业务数据库在只读、可重复读事务中检查，零业务写入、零模型调用，不创建人工标签，不更改已有数据或索引。快照时间约北京时间2026-10-12 22:02，线上回查结束约22:04。后台采集继续运行，未来总数会变化。

| 检查 | 范围 | 结果 |
|---|---:|---|
| 当前真实非删除文档与当前版本 | 2418篇，包含AI相关与非相关资料 | 哈希、当前历史版本、标题、正文、URL、类型及演示标记与保存payload一致，未发现完整性问题 |
| AI相关文档自动结构/出处监测 | 1698篇，全部文档类型 | 扫描完整，结构/出处错误0；CVSS缺失分数1558条、缺失向量92条警告 |
| 独立读取OSV/NVD原生结构对照 | 1455篇、5071个有值或有预测的字段 | 5071个字段与保存的原生来源记录一致；不是独立人工事实准确率 |
| CVSS 3.0/3.1基础分复算 | 106个同时有可计算向量与数值的条目 | 106个一致，无分数差异 |
| 其他CVSS条目 | 1047个有效V3向量缺来源分数；591个其他版本或缺向量 | 未凭空补分数；不支持或不完整的条目不计入复算成功分母 |
| 有限外部回查 | 40篇，覆盖14适配器/7类别 | 18篇OSV/NVD原生字段一致，17篇确认来源身份但只做部分内容检查，2篇网络失败，3篇标题严格匹配需补充核对 |

源记录对照直接读取原始OSV/NVD JSON，没有调用生产采集解析器生成“预期答案”。规范化事实粒度复用现有协议；这仍只能验证提取/保存一致性，无法证明原始发布者的结论无误。没有事实的字段不凑入有值字段分母。

CVSS按[FIRST官方V3.1公式](https://www.first.org/cvss/v3.1/specification-document)7.1/7.4及附录A复算基础分，基础计算兼容V3.0。已执行6个已知向量自检，覆盖9.8、7.5、0、10、6.1及不支持的V4。未复算V2/V4、环境/时间分数；未核验CVSS攻击条件是否符合真实漏洞。

回查使用[OSV官方按ID接口](https://google.github.io/osv.dev/get-v1-vulns/)、[NVD官方接口](https://nvd.nist.gov/developers/vulnerabilities)及原公开来源。复用现有Redis共享来源限速、有限重试及TLS验证；Redis只产生常规限速记录。没有改游标、调用采集任务或写入数据库。

## AI辅助审阅发现的具体问题

以下是本次助手根据保存正文、规则命中原因与公开原文作出的审阅意见，**不是独立人工金标**，未写入`EnrichmentQualityReview`。

### 1. 就业培训政策被归为AI安全情报

- 文档ID：`e23f128a-37f5-48b5-8efb-079baa943e5e`。
- 来源：[TechFirst Local](https://www.gov.uk/government/publications/techlocal)。
- 已存状态：`ai_relevant=true`；正文命中AI/artificial intelligence与security，风险分类为空。
- 原文主题是数字技能、就业及资助项目。AI与网络安全是并列产业方向，没有据此说明AI系统安全风险。
- 判断：来源与内容真实，但AI安全主题相关性明显不足；属于共现词误收，不是正文造假。建议候选待审/降权，避免直接进入安全问答的主要证据。

### 2. 密码学工具Merlin被当成AI产品

- 文档ID：`acf97369-33fe-43f5-b017-6603089c14fc`。
- 来源：[SequenceHash: multihashing for the rest of us](https://blog.trailofbits.com/2026/10/02/sequencehash-multihashing-for-the-rest-of-us/)。
- 已存状态：`ai_relevant=true`、`risk_categories=["ai_infrastructure"]`，规则命中`body_products=["merlin"]`，没有AI正文词命中。
- 原文讨论哈希输入编码与Fiat-Shamir工具，Merlin在这里是密码学工具，不能据同名词判断为NVIDIA AI产品。
- 判断：产品词歧义造成明确误收。产品识别应结合厂商、组件及上下文，不能仅使用裸产品名。

### 3. 两篇论文需要定义收录边界

- `d19dcc2d-dc8c-4a5e-8095-5c4a9e48715f`，DOI `10.11591/ijpeds.v11.i1.pp417-424`：人脸识别门禁，主要是AI用于物理安防，摘要未描述AI系统本身的漏洞或风险。
- `08c1c485-e196-443d-bb89-56c06f87d6bb`，DOI `10.1145/2736285`：硬件PUF抵抗机器学习建模攻击，属于AI辅助攻击硬件的交叉边界。

这两篇不直接认定为错误数据。团队应明确是否纳入“AI用于安全/AI辅助攻击/AI自身安全”不同范围，并保留类别，避免混为同一检索主题。

上述发现来自有意分来源的小样本审阅，不能使用“2/40”推算全库准确率。Cisco/SANS抽样还包含明确非AI相关的对照记录，不能把40全部作为AI正例。

## 未完成的外部核验与补充查阅

- 两篇Hugging Face讨论（`post-263389`、`post-261887`）在本机访问官方主题JSON时`ConnectTimeout`，本轮未验证外部当前内容；不能计为数据错误或核验通过。
- NIST SP1353：严格完整标题匹配未通过，但保存摘要在页面存在；补充查阅[官方页面](https://csrc.nist.gov/pubs/sp/1353/ipd)确认文档身份与初始公开草案状态。存储标题把草案状态紧贴正文标题，属于展示规范化问题，不是不存在的标准。不得将其宣称为最终正式标准。
- SANS Wordfence：RSS标题带日期后缀，严格整串匹配失败；补充查阅[官方文章](https://isc.sans.edu/diary/33382)确认标题与正文主题。该标题匹配告警不是数据造假。
- SANS Stormcast `33398`：自动标题/摘录匹配未确认，补充访问仍未获取可核验内容，保持未知。

两条补充页面查阅由助手完成，不作为人工金标，也未改写自动回查原始报告。HTTP成功、标题一致不等于逐句核验了修复建议、POC或风险结论。

## 结果与复用

本地详细报告（被Git忽略，不上传原始审计输出）：

- 离线：`output/enrichment-quality/accuracy-audit-20261012T140127738370Z.json`。
- 完整：`output/enrichment-quality/accuracy-audit-20261012T140420399740Z.json`。

新增只读脚本：`scripts/audit-data-accuracy.py`。使用方式：

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts\audit-data-accuracy.py --self-check
& 'D:\cit\.venv\Scripts\python.exe' scripts\audit-data-accuracy.py --offline
# 有限40条左右的官方来源回查；会等待现有限速与网络超时。
& 'D:\cit\.venv\Scripts\python.exe' scripts\audit-data-accuracy.py
```

建议后续优先修复主题相关性及产品同名歧义，并以当前冻结样本建立独立人工复核。此时系统人工审核记录仍为0，因此比赛口径下的全库准确率、独立召回率仍未知。本次仅检测，没有删改情报、重新索引、重启服务或修改业务分类规则。
