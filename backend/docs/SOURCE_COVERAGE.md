# 七类来源与数据质量边界

类别是本项目为赛题覆盖采用的定义，不是比赛指定唯一分类。多个漏洞数据库仍算一类；离线样本不算真实来源；配置 URL 与采集成功、AI 相关性、完整正文是不同指标。

| 类别 | 官方公开来源 | 采集方式/增量 | 权利与内容边界 |
|---|---|---|---|
| vulnerability | https://api.osv.dev/v1/query；https://services.nvd.nist.gov/rest/json/cves/2.0 | OSV 包/page_token；NVD lastMod 固定窗口、关键词/startIndex，带时间重叠 | 结构化漏洞记录/原始出处。NVD API Key 可选，仍共享限速；不声称覆盖所有 AI 产品 |
| vendor | Cisco PSIRT；https://www.nvidia.com/content/dam/en-zz/Solutions/product-security/product-security.json | Cisco RSS 条件请求；NVIDIA 官方 JSON 目录筛选 AI 产品/CVE 的十条摘要 | NVIDIA 正文页面返回 403，未绕过；只保存真实目录行、公告链接、日期精度/严重度，未称完整公告 |
| community | SANS ISC；https://discuss.huggingface.co/search.json | SANS RSS；HF 公开 Discourse 搜索提示注入主题，最近三页分页 | 普通 AI 营销/翻译帖子排除；安全讨论摘要不等于全文，社区断言不当厂商事实 |
| blog | https://blog.trailofbits.com/index.xml | RSS 条件请求，优先 content:encoded，截断标 partial | RSS 发布的公开内容，不宣称可再发布全部原文；保留引用/来源 |
| academic | https://export.arxiv.org/api/query；https://api.crossref.org/works | arXiv lastUpdated 排序分页及 watermark/一天重叠；Crossref from-index-date/cursor（每轮最多五页，保留游标下轮续接，过滤无 AI/安全语境的结果） | arXiv API 摘要/作者/类别/DOI；Crossref 许可随记录保留，摘要可能受版权限制；均未下载论文全文 |
| standard | https://airc.nist.gov/airmf-resources/airmf/ 及已登记章节 | 固定官方章节轮换、正文变化 hash | AI RMF 自愿框架；不是 ISO 认证或所有新标准发现服务 |
| policy | GOV.UK AI cyber security Content API；https://www.gov.cn/zhengce/zhengceku/202307/content_6891752.htm | GOV.UK 白名单栏目与有限子路径队列；中国政府网固定法规正文条件请求 | UK 指导/政策与 CN 部门规章分别标法律性质，后者 effective_at=2023-08-15；固定目录非全部法规全量发现 |

官方 API/访问依据：[NVD](https://nvd.nist.gov/developers/vulnerabilities)、[arXiv API](https://info.arxiv.org/help/api/user-manual.html)、[Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[GOV.UK Content API](https://content-api.publishing.service.gov.uk/reference.html)、[GOV.UK 内容复用](https://www.gov.uk/help/reuse-govuk-content)。法规原发布页面部分来源曾返回 403，使用可公开访问的中国政府网正式转载；未绕过防护。

## 质量与可靠性

固定 URL/路径白名单、TLS 校验、禁止重定向、响应大小≤8MB、连接/读取超时、每请求最多三次重试。Redis 来源级共享限速：NVD 至少 7 秒、arXiv 3.1 秒、其他 1 秒；多个同端点来源共用限制。HTTP/network/processing 错误分别记录，单源失败不阻断其他源；原文、版本、证据、知识事件与游标同一事务提交，异常空页不跳游标。重试存在但不能解决永久网络/权限阻碍。

相关性是 rule-v4：AI 产品/正文语境加安全/风险证据；普通 AI 营销和 AI 发现漏洞宣传链接排除，只有标题/元数据不作为证据。风险类别为规则标签，不是模型置信度。各适配器实际采集数量、规则相关数在受保护 `/ops/metrics` 查询；七类都有规则相关记录不等于独立审核确认或评分已得分。

full_text 是所获取记录/章节的完整正文，不意味着全文 PDF。summary、metadata_only、partial、unknown 均显式返回；来源未给精确发布时间则 null，保留已知日期精度和发布日元数据。当前 first_collected_at 是首次成功入库时间，任务 started_at/步骤耗时/finished_at 提供采集处理观测；没有历史首次观测或精确发布时间时不补造。

## 监测边界

本机已启用真实来源，多数每小时，arXiv 每天（官方 API 缓存）；还有增量续页。源数据延迟、RSS 只保留最近项、已登记规范/法规目录、初始关键词/产品窗口与外部服务限流均影响召回。不能保证所有 AI 安全文章或新法规都被发现。

监控把缺失/低精度发布时间和负时差分开，历史首入库与近期到达分开；按到达时差划分的 cohort 仅用于观察，不当独立随机 SLA 样本。此次有超过 6h 的数据，尚未达成持续 ≤6h 全覆盖。要评分需固定观察期、来源范围/发布时间定义、成功与失败分母、人工遗漏清单，不能用轮询间隔替代。

## 富化边界

affected（产品+版本一个维度）、CWE、版本化 CVSS、修复/补丁链接、技术分析链接均真实有源字段；NVD 另有公开 Exploit 标签链接，作为 POC 元数据，未运行。链接存在不表示后端执行了分析或复现。未知值不编造 CVSS 分数。来源冲突保留版本/多方证据；关联论文的语义判断留给 Agent，引用/方法/审核状态由后端保存。

规则 v4 已对已有原文重评，保留全部原文/版本/证据，仅调整规则标签和基于当前文档的漏洞相关性。规则误判修复有回归测试，但未声称独立标注后的准确率。先前已入库的无关元数据保留历史，不参与相关情报成绩。
