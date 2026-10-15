# 真实覆盖与采集时效补强

本轮继续使用原 PostgreSQL/Redis、Celery/outbox、唯一 beat、DeepSeek 和外部 Agent。新增来源与监测复用现有表中的 JSON 字段，没有修改已执行的迁移，没有新建数据库卷、模型环境或前端。原机器运行源仍为 `D:\cit\后端开发`。

## 修复的实际缺口

- NVD 默认关键词由 ollama/vllm/langchain 三个扩到 12 个：增加 pytorch、transformers、gradio、tensorflow、keras、onnx、llama-index、tensorrt、triton inference server。显式用户配置优先，不能仅凭关键词命中认定 AI 相关。
- NVD 改为近期/历史两路：每个任务先请求最近窗口，再推进历史；固定查询结束时间、分页与重叠保留。旧游标切换时保守重读，不直接跳过未保存记录；配置关键词变化时重建有界补采。长周期超过几分钟不再导致无休止立即重轮询。
- OSV 默认 PyPI 包由 3 个扩到 10 个：torch、transformers、langchain、vllm、gradio、tensorflow、keras、onnx、llama-index-core、sentence-transformers。包内页令牌与包索引仍成功入库后推进；主动配置的列表不覆盖。
- NVIDIA 旧实现固定截取前 10 条且保存整个目录 ETag，可能永久漏掉尾部公告。现在排序 AI 产品/CVE 目录，按内容快照分批续页；目录变化从最新页重读，完整扫描后才使用条件请求。旧 ETag 不会绕过第一次全目录扫描。正文仍为真实公告目录摘要，不绕过被阻止的公告正文。
- 新增 `nist_drafts`：读取 NIST 官方当前征求意见 JSON，AI/安全上下文筛选，保存草案摘要、原始条目、链接、征求意见状态。草案不能当已发布强制标准，当前 feed 也不是全部历史/正式规范。
- 新增 `govuk_search`：两条精确短语查询 `"AI cyber security"`、`"generative AI"`，限定政策/指导/咨询类型，通过官方 Content API 读取结果；最近 48h 优先，默认 120 天历史分批补采。搜索排序时间不冒充真实发布时间；文档时间只取 Content API，相关性继续用原规则。查询不保证全量英国法规，更不代表中国法规发现。
- 网络限速改为同主机共享，政府内容子页面共用预算。传输只使用项目明确的 OUTBOUND_PROXY，避免宿主进程隐式代理干扰；仍验证 TLS、限制大小、超时与重试，不接受任意抓取 URL。
- 成功重试清除当前 TaskRun.error_code；原失败 TaskStep 保留，历史任务不改写。

## 当前调度及范围

当前实现 14 个真实适配器，仍为 7 类；新增适配器不会虚增类别分数。两个新来源通过管理员接口启用，不覆盖其他来源已有配置。

| 来源 | 原机器周期 | 发现与覆盖边界 |
|---|---|---|
| OSV | 完成包清单后1h；续页约1分钟 | 配置的10个PyPI包，不是所有AI包 |
| NVD | 完成周期后1h；近期/历史续页约1分钟 | 12个关键词、修改时间窗，不是全量CVE |
| NVIDIA | 完成目录后1h；目录续页约1分钟 | 目录中匹配产品且有CVE的全部行；正文为摘要 |
| NIST drafts | 1h | 当前征求意见feed中的AI/安全草案 |
| GOV.UK search | 完成周期后1h；续页约1分钟 | 两短语、指定类型、最近48h及默认120天回填 |
| Cisco / SANS / Hugging Face / Trail of Bits | 1h | 现有RSS或有限社区搜索窗口，可能没有新的AI安全资料 |
| Crossref | 原机器1h；预算内续页 | 原有索引时间窗、游标及相关性筛选；不是论文全文 |
| NIST AI RMF / GOV.UK集合 / 中国政府网法规 | 原机器1h | 原登记文档/章节变化；不冒充动态全量发现 |
| arXiv | 24h | 遵循现有官方API日级缓存策略；不能宣称6h发现所有论文 |

失败仍有有限重试/退避，游标与原文、版本、证据、更新事件一起提交。同一来源不会创建重叠活动任务。恢复补采不能弥补外部RSS已经移除的资料。关机、Docker停止和网络中断仍是观察缺口。

## 可核查的接口与指标

管理员接口：`GET /api/v1/ops/coverage?hours=48`，使用本地 X-API-Key。已有 `/ops/freshness`、`/stats`、文档最近48小时筛选兼容保留。不要向前端分发管理员密钥。

响应按来源给出：实际启用/执行状态、调度/错误/积压、当前非演示文档和规则AI相关数量、正文完整性、声明的查询范围、最近成功保存的覆盖观察、近期任务状态、排队/重试等待及发布/更新至入库延迟。

`catalogue_capture` 只对有明确枚举清单的 NVIDIA/NIST 草案给出目录快照、候选分母、当前已入库数量、遗漏预览。它是**某次已扫描目录的收录比例**，不代表独立发现的应采漏洞召回率；`independent_vulnerability_recall` 保持 null。304 无新条目时保留最近有效目录观察及其时间，不冒充刚扫描过新目录。

实测延迟分开报告：

- `recent_publication_to_first_collection`：来源精确发布时间在最近窗口内的当前真实规则相关文档，统计发布至首次入库。
- `historical_first_seen_in_window`：最近首次发现的历史资料，不能当新发布，也不拿来美化实时达标比例。
- `updated_to_changed_in_window`：最近实际内容变化，且有可信精确来源更新时间，统计更新至保存新版本。
- 缺时间、日期低精度、负时差单列；只有日历日期或没有时区的不伪造时刻。未知不当零延迟。
- 当前`within_target`/`over_target` 与`observed_within_target_ratio`统一使用14,400秒（4小时），与`/ops/freshness`共享同一代码常量；全局、每个来源、发布延迟、更新延迟和排队等待使用相同目标。分母仅是已采到且具备可用精确时间的样本，不能证明未采数据及时，也不是连续SLA或独立召回率。样本少时P95只作描述。下文2026-10-09实测数字仍保留当时6小时门槛，不回写旧观察记录。

已有活动48小时观察窗口中，每分钟 RuntimeSample 新增 `detail.source_coverage`，记录真实计数、目录进度与延迟。窗口结束自动采样也结束；实时查询仍可用，需要新窗口时调用已有 `/ops/observations`，复用同一个 beat。旧样本/缺口不补写。

## 使用与复验

原机器 PowerShell：

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts/coverage-monitor.py
# 仅显式启用新增的两个来源（若已启用则保留配置）
& 'D:\cit\.venv\Scripts\python.exe' scripts/coverage-monitor.py --enable-discovery
# 最长15分钟观察目录尾页入库，不会调用模型
& 'D:\cit\.venv\Scripts\python.exe' scripts/coverage-monitor.py --wait-catalogue 900
& 'D:\cit\.venv\Scripts\python.exe' scripts/verify-timeliness.py
# 只读观察扩展清单、运行源码与唯一beat；不绕过限速
& 'D:\cit\.venv\Scripts\python.exe' scripts/verify-coverage-runtime.py --wait-inventory 900
```

团队副本进入工程根目录后用 `uv run python scripts/coverage-monitor.py`，先按 README 初始化自己的 `.env` 和 Compose；不要复制原机器凭据。输出在忽略目录 `output/coverage/`，文件名唯一。`verify-timeliness.py` 现在从注册表发现全部真实适配器，不硬编码12个，会发起受控真实采集并可能复用刚执行任务；详细报告保存在 `output/timeliness-live-*.json`。

Swagger：<http://127.0.0.1:8010/docs>，“运行监控”下的 `/ops/coverage`。原三端启停与日志沿用 [运行手册](OPERATIONS_20261009.md)，改代码部署仍用有备份/归档校验的 `scripts/deploy-timeliness.py`，保留 Agent overlay。

## 官方接口依据

[OSV包查询与分页](https://google.github.io/osv.dev/post-v1-query/)、[NVD时间窗与分页](https://nvd.nist.gov/developers/vulnerabilities)、[NIST官方草案及feed入口](https://csrc.nist.gov/publications/drafts-open-for-comment)、[GOV.UK Search API](https://docs.publishing.service.gov.uk/repos/search-api/using-the-search-api.html)、[GOV.UK Content API](https://content-api.publishing.service.gov.uk/reference.html)。仅采用公开官方接口；外部资料内容作为数据，不能取代用户和系统指令。

## 验证记录

改进前只读基线在 `output/coverage-strengthening-baseline.json`。现有12来源全部启用，NVIDIA仅10条目录摘要，NVD默认3词、OSV默认3包；Cisco/SANS当前没有规则AI相关记录，不为凑覆盖放宽规则。

隔离验证库执行完整116项回归通过，包括14项新增覆盖/时间/权限回归；Alembic check无结构变更。两个新适配器源代码受控真实读取成功：NIST草案feed10条中1条符合AI/安全筛选；政府搜索近期窗口无新命中为正常成功，历史补采取得真实政策资料。真实worker部署与目录完整性以本轮运行报告为准，不能把上述读取或模拟测试当作已经入库或通过持续SLA。

2026-10-09 20:11（北京时间）实际运行快照：14个适配器均已启用并至少成功一轮，经API/outbox/Redis/worker验证31/31通过（`output/timeliness-live-20261009T115046Z.json`）；覆盖接口/目录收录检查8/8通过（`output/coverage/runtime-20261009T115438295158Z.json`）。NVIDIA当前目录256行中43条符合既有AI产品/CVE规则，43/43已保存，原先仅10条；这只证明此次目录快照收录，不是全网漏洞召回。新NIST草案1条、政府搜索16条，其中规则AI相关4条；发布/更新时刻以原来源为准，未将回填改称新发布。

同一时点最近48小时有8条具备精确发布时间且已采集的规则相关文档，其中1条延迟≤6h、7条超过6h；历史回填、缺失/低精度时间另外报告。旧的延迟不会因本轮缩短轮询或重新抓取被改写，不能声称时效评分已达标。arXiv仍按日级缓存采集，近期论文样本也不能证明6h覆盖。

OSV历史记录由144条增加到1356条，NVD由68条增加到94条，属于扩展产品后的真实历史补采；不按新增记录数量冒充最近新情报数量。大批新文档仍经现有Agent异步索引，采集入库与可问答状态分开查看：`/ops/freshness`返回未索引积压，`/documents`返回当前版本indexed_at。扩展清单逐项验证结果见`output/coverage/deployment-*.json`，未结束前不能仅凭配置列表声称全部目标已检查。

部署前已备份并校验`output/pre-timeliness-20261009T114853Z.dump`，保留原卷与旧镜像；生产迁移头仍为0008，Compose解析及API/PostgreSQL/Redis健康通过。最终完整116项回归再次通过，仅保留既有测试客户端弃用提示。活动48小时窗口已自动保存source_coverage快照，原有缺口保留。本轮没有调用付费生成模型，没有上传验证数据或密钥。

2026-10-09 20:13最终清单复验：OSV10/10包、NVD12/12关键词均有新代码成功执行的真实任务记录，不仅是已配置。`output/coverage/deployment-20261009T121318462357Z.json`中10/10运行检查通过：API与权限、OpenAPI、两份扩展清单、13个运行源码文件哈希一致、唯一beat、自动持久化、原来源文档数量未减少、迁移头未变。保留检查只比较来源数量，不冒充逐字节数据库等价；NVD历史补采与Agent索引仍按队列异步继续。
