# 主题误收与检索范围同步修复（2026-10-13）

已在现有后端部署rule-v5，使用原PostgreSQL、Celery/Redis、唯一beat、真实缓存E5和DeepSeek配置。未删除原始文档、版本、证据或数据库卷；本轮没有付费模型调用。

## 修复内容

- 产品词消歧：Merlin/NeMo/Riva需匹配厂商或明确产品用途，不能把密码学Merlin等同名实体当作AI产品。
- 普通AI与security词共现不足以证明AI安全主题；待审资料保留原文并标注`triage_state=needs_review`与原因。
- 工业注塑、抑制剂注入及化学材料名LLM-105不会被直接当作提示注入或语言模型风险。
- 补充language models、prompt-injection、复数injections、指令层级等真实术语；长摘要中的模型与攻击不再因任意字符距离被拆散。
- 返回`topic_scope`，区分AI系统安全、治理、AI辅助安全、物理安防、AI辅助攻击及相邻主题。交叉领域资料保留类别，不将其一概删除，也不声称全部为AI系统自身漏洞。
- 原文未变而相关性改变时，在同一事务产生索引撤回/更新事件；旧租约作废，防止旧ACK复活已排除的资料。外部索引撤回只对检索范围生效，原始资料仍可访问。
- 新Agent回答不能引用已排除的资料；内部调度与索引完成也检查当前相关性。已有历史回答保持原样。
- 修复“已有同版本范围撤回事件后再执行文档逻辑删除”未设置删除标记的边界。

## 测试与实际验证

- 专用新数据库完整迁移/契约/安全扫描及回归：**184 passed，1条既有依赖弃用警告**，15.67秒。新增合成回归覆盖22组分类对照、长摘要、交叉类别、撤回/恢复、重复投递和旧租约ACK。
- 部署前数据库备份及归档核验：`output/pre-timeliness-20261013T055231Z.dump`。本次没有新迁移，业务库保持0010。
- 已审查并应用2439篇真实非删除文档的版本固定计划，分类元数据更新；其中6篇退出默认AI检索范围，11篇进入候选范围。候选判断不是独立人工准确率成绩。
- 6条撤回事件全部由真实Agent执行并确认`invalidated`，11条更新事件全部确认`indexed`。再次调用Agent索引验证接口，17条实际索引证明全部通过。
- 17篇变更资料的原版本哈希均保留、原文接口均200；没有设置文档删除标记。
- 运行中API/worker/beat相关8个源码文件哈希与源工程一致。API/Agent健康、4小时时效目标、完整富化监测仍通过；当时漏洞监测范围1450篇，数量随采集变化。

本地记录（忽略目录，不上传备份/实际审计输出）：

- 计划：`output/relevance/plan-20261013T055413312476Z.json`。
- 应用：`output/relevance/applied-20261013T055523273408Z.json`。
- 实际验证：`output/relevance/runtime-20261013T055559238437Z.json`。

## 后续使用

```powershell
cd 'D:\cit\后端开发'
# 默认只读，冻结当前版本和原判定指纹。
& 'D:\cit\.venv\Scripts\python.exe' scripts\reassess-relevance.py
# 审阅计划后显式应用；旧版本/旧判定冲突会整笔回滚。
& 'D:\cit\.venv\Scripts\python.exe' scripts\reassess-relevance.py --apply --plan <计划路径>
& 'D:\cit\.venv\Scripts\python.exe' scripts\verify-relevance-runtime.py --compose-project cit-backend-development --applied <应用记录路径> --wait-seconds 60
```

计划默认排除演示数据。相同计划重复应用跳过已一致的分类，不重复制造索引事件。不能手工把失败或pending事件标成indexed；由Agent实际索引验证后确认。
