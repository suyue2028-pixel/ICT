# 五分钟后端闭环演示

使用现有三端，本轮没有制作新页面。先按 `OPERATIONS_20261009.md` 启动三端，确认主界面 http://127.0.0.1:5173、问答 http://127.0.0.1:5173/qa、Swagger http://127.0.0.1:8010/docs 可访问。

## 演示前检查

```powershell
& 'D:\cit\.venv\Scripts\python.exe' 'D:\cit\ICT-team\scripts\run_fullstack_windows.py' status
```

检查 Agent 为 live 模式，sync 正常运行。仅状态检查不调用收费模型。不要把管理密钥放进前端、投屏的终端或材料中。Swagger 证据接口需要普通用户 Bearer；脚本会在内存中申请临时用户，结束即撤销。

## 0:00—1:00 数据来源和关联

说明不同来源里的同一 CVE 通过明确标识关联，文章同时提到多个 CVE 不把漏洞合并。展示情报详情、来源 URL、资料完整性和版本。NVIDIA目录只作目录摘要，不能讲成已获取被拒绝的公告全文。

## 1:00—3:00 真模型问答和追问

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts/demo-closed-loop.py --scope real --ask --via http://127.0.0.1:5173
```

脚本从当前库自动选取至少两来源、已索引的真实 CVE，通过前端代理使用的 `/api/v1/agent/query` 最多请求两轮，记录生成模式、模型HTTP次数、消息顺序，并逐条检查引用字符区间。

第一问主要风险；第二问“它影响哪些产品和版本，如何修复”。展示它沿用同一会话。如果资料没有修复版本，应明确未知，不能临时编造修复建议。实际输出保存在 `output/traceability/demo-real-时间/report.md` 和 captures.json，适合录屏/人工核对，均不含请求凭据。

这不是一组全库评分测试。两轮真实模型请求说明链路可用，不能证明问答全库准确率、召回率或每条推理均正确。

## 3:00—4:00 回到原文证据

从回答 invocation_id 获取 `/api/v1/agent/invocations/{id}/evidence`，显示 document_id/content_hash/source_url/locator/position、关联字段和版本URL。打开版本URL核对摘录，说明 current/historical 状态。`revision_json` 中的 CVSS 等结构化原始证据也能逐字符核对。

原文完整性与语义支持度分开说：当前程序检查引用真实、位置正确；每条结论是否恰好由该引用支持仍需审阅。可以展示修复前回归用例，说明伪造位置或跨用户获取调用记录会被拒绝。

## 4:00—5:00 可靠性与边界

展示最新 CI 本地报告、独立故障演练 result.json 和恢复报告：Redis中断不会丢掉已接受任务；恢复后补发；AOF/正常数据库重启留存验证成功。独立演练项目没有第二个 beat，生产仍只有一个。

明确尚未完成：GitHub Actions 尚未在远端执行；48小时观察仍以观察记录为准，有停机缺口；独立人工金标准与全库准确率尚未完成；没有做整机丢失灾难恢复。本轮不把一次演示当长期稳定性证明。

## 无外网/不调用模型的替代展示

```powershell
cd 'D:\cit\后端开发'
& 'D:\cit\.venv\Scripts\python.exe' scripts/demo-closed-loop.py --scope demo
```

它执行 API202 → 数据库/outbox → Redis → worker → 三份明确虚构资料 → 关联/版本查询，不调用 DeepSeek，也不生成模拟答案。得到7项流程检查报告；不能把它讲成真实 Agent 生成演示。

若要演示 mock 回答契约，使用隔离 CI 测试中的 `test_answer_evidence_owner_current_and_historical`，明确标注测试替身。真实问答无法联网时，展示上次已保存、标明日期的真实报告，不能声称刚刚完成了模型调用。请勿为演示运行 POC 或攻击验证。
