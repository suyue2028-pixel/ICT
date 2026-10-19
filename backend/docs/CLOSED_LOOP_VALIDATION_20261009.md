# 2026-10-09 闭环、CI、故障恢复实际验证

最终交付统一位于 `D:\cit\后端开发`。本机 API、worker、beat 已使用更新后的共享镜像，迁移头 `0009_association_evidence`。原PostgreSQL16和数据卷保留；未处理GitHub、未改队友Agent源码、未制作前端。

## 验证结果

| 检查 | 实际结果 | 本机证据 |
|---|---|---|
| 本地CI等价检查 | 126 passed、0 skipped；迁移/模型一致、10份模型schema与OpenAPI无漂移、Redis PING和API就绪通过；已有一条依赖弃用警告不影响通过 | output/ci/local-result.json、pytest.xml |
| 依赖/CI配置 | uv0.12.19对现有Python3.12.14离线核对锁文件通过；固定Action SHA；在独立目录执行workflow原样的临时.env创建片段，Compose无本机.env时解析通过 | output/ci/compose-without-local-env/result.json |
| 真实自动关联→回答→原文 | 20/20；通过5173前端代理发出两轮真实Agent请求，服务报告4次DeepSeek HTTP调用；两来源原文、每轮3条引用、同一会话4条有序消息通过 | output/traceability/demo-real-20261009T124000Z/result.json、report.md、captures.json |
| 最终类型化接口回查 | 13/13；不再调用模型，回查上述已保存回答的原文/JSON字符位置、版本与当前状态；匿名401、其他用户403；临时令牌撤销 | output/traceability/final-evidence-replay.json |
| 虚构离线流程 | 7/7；API202→outbox/Redis/worker→3份虚构资料→幂等与关联版本查询；0模型调用，未称为Agent生成成功 | output/traceability/demo-demo-20261009T124127Z/result.json |
| 独立真实容器故障 | 12/12；Redis中断时持久任务/outbox保留，失败不推进游标；恢复补发；worker暂停期间队列AOF重启留存；重复导入不重复版本；正常PG重启保留hash/关联依据；无新beat | output/reliability/verify-fault-20261009t125155z-713dee/result.json |
| 故障发布/恢复耗时 | Redis不可用时发布返回5.734s（测试限制20s）；恢复后任务8.140s完成，含主动等待退避；一次隔离演练，不是生产RTO/P95 | 同上 |
| 新版本数据库备份恢复 | 一致快照恢复到新的verify_restore_20261009t125209z_5a0b6742；22张表计数和SQL行内容指纹一致，含新关联依据和会话记录；恢复库API返回2385份真实文档，与快照一致 | output/recovery-20261009T125209Z.json、.dump |
| Agent索引备份 | SQLite online backup，quick_check=ok；所有表计数一致，原索引不替换，0模型生成调用 | D:\cit\ICT-team\reports\local\windows-fullstack\index-recovery-20261009T124002Z.json |
| 最终运行实例 | API/worker/beat所有Python源码和迁移逐文件哈希比对、共享镜像、唯一beat、健康和beat→Redis→worker→DB真实心跳，均已核验；最终结果见报告 | output/traceability/runtime-deployment.json |

测试数据库为 `verify_ci_20261009t123555z_c4777c12`，测试数据事务回滚；恢复副本和独立故障卷保留，不覆盖原库。历史测试报告没有当成本轮结果。公开源码候选扫描未发现已配置凭据或常见令牌模式，但不宣称覆盖所有秘密格式。

## 实际两轮例子与证据边界

脚本自动选择当前库里已索引、至少两个来源的 `CVE-2025-33213`。第一轮回答NVIDIA Merlin Transformers4Rec Trainer反序列化风险，引用NVD正文及NVIDIA目录；CVSS8.8信息定位到保存版本的原始JSON字段。第二轮延续同一对象询问版本及修复，返回partial并明确修复版本未知，没有编造厂商修复公告。

两个来源的旧版本关联依据由保存的RawRecord重建，接口明确标记 `reconstructed_from_revision`，未冒充新迁移之前已记录的关联过程。新虚构样本的关联依据实际写入新增表；版本变化、旧边移除、原文旧版本留存、文章多CVE不合并均有事务回滚测试。

引用真实和位置正确是本轮程序验证结论；没有独立人工标注，因此不能把这20项流程检查算成问答准确率或语义召回。该示例展示一条真实链路，不覆盖所有数据类型或全部跨文档推理。

## 仍需后续完成

- GitHub Actions尚未推送/远端运行。未来上传时，workflow必须进入团队仓库根`.github/workflows/`；本轮只完成本地CI配置与等价验证。
- 48小时观察仍按实际观察窗口记录，尚不能提前称为完成或消除停机缺口。采集≤6小时SLA和独立人工金标准仍未证明。
- 备份恢复覆盖数据库/索引快照和隔离服务故障，不是整机丢失、凭据失效、模型缓存丢失的灾难恢复演练。
- 前端可接新增证据接口做原文跳转/历史标记；本轮已在现有前端代理入口验证，不开发新页面。
- Agent无需变更V1传输；请队友审阅Unicode locator规则、引用支持性和未知字段展示。当前未知字段提示仍可能带Agent内部doc标签，建议队友改为文档标题与中文字段名。

操作入口见 [闭环、CI与恢复说明](TRACEABILITY_CI_RECOVERY.md) 和 [五分钟演示讲稿](DEMO_RUNBOOK.md)。
