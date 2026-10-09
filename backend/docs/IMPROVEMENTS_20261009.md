# 2026-10-09 后端改进与验收

本次检查发现 Windows 前端、Agent、同步进程和 Docker 引擎均已停止。历史记录不能作为今日运行证明。现有未提交改动保留；本次不处理 GitHub。

| 已有功能 | 检查时实际状态 | 缺口 | 本轮验收方法 |
|---|---|---|---|
| E5 混合检索、DeepSeek、引用校验 | 代码存在，服务停止 | 12 问题评测规模小，基线读取 HEAD | 固定提交和语料哈希，至少 30 问题，独立验收分组 |
| Agent 网关、用户会话与预算 | 现有测试覆盖部分行为 | 跨用户续写、异常响应和契约一致性需加强 | 回滚事务内的权限/失败/契约回归与真实入口联调 |
| 12 适配器、时效性与外部索引 | 需恢复后实查 | 缺持续观察记录和缺口统计 | 同一 beat 采样、持久化观察窗口、记录缺口 |
| 字段证据与原始 CPE | 已保留原文 | 产品和范围需保守结构化 | 增加派生查询，不修改历史版本，不推断安全版本 |
| 部署前备份 | 有历史备份 | 未完成本轮隔离恢复演练 | 新建专用恢复数据库，比较数据和迁移版本 |
| 分页和后台流水线 | 已有实现 | 缺受控性能记录 | 有限请求、独立测试数据、披露样本量 |

## 实际完成结果

- 后端新增15项回归后85通过，再增加静态契约一致性和严格观察请求校验，共 **87/87**。Agent 英文回归与回退保护共 **91/91**。原有数据和队友改动保留。
- 36问题虚构评测（24开发/12保留），12项行为检查通过。固定基线为9daad1c完整提交，记录语料/代码哈希、模型revision与参数；保留组返回结果相关比例45.24%→85.71%，Recall@3保持100%。固定分母P@3保持38.10%。这是草稿标注小样本，不能当作全库评分。
- 通过真实前端入口九组问答，七completed、一partial、一证据不足；修复后21次模型HTTP尝试，今日两个基线6次，共27次。调用持久化、会话与就绪17/17通过。首次只读查询曾超时，后续复验通过，不重复付费问答。
- 产品/版本范围通过只读normalized查询保存原始证据对应关系；NVD与OSV真实样本各1条通过API核对。NVD上界不冒充修复版本，复杂布尔适用性仍未执行。
- PostgreSQL一致性快照恢复到新验证数据库成功，表行数/迁移一致，恢复库FastAPI列表200。Agent SQLite备份quick_check=ok，各表行数一致。没有覆盖原库或原索引。
- 20次列表读取（并发2）全部200，中位44.33ms；3次20条虚构流水线首次新增、之后幂等，事务回滚。未报小样本P95或真实Celery吞吐。
- 实际运行核对 **11/11**：API/前端/Agent就绪、严格鉴权和请求、唯一beat、7份运行源码哈希一致、观察自动采样、验证数据隔离、12适配器/7类来源、所查前端文件及日志无本地已知凭据匹配。检查时真实文档1076，当前AI相关版本未索引数0。
- 迁移新增0007（仅观察表）；Alembic check无新操作。部署前备份 `output/pre-timeliness-20261009T064701Z.dump`、旧应用镜像标签保留；最新容器使用本源目录。

## 仍需验收/协作

| 项目 | 当前边界 |
|---|---|
| 48小时稳定性 | 10月9日14:28至10月11日14:28，北京时间；观察中，已记录两段缺口，不能判持续通过 |
| 问答与引用质量 | 典型公开原文已做AI辅助核对，独立人工评分仍待完成；工具审计回答有间接记忆审计内容，需审查 |
| 版本适用性 | 能解析命名与边界，但不执行复杂CPE布尔/否定、资产匹配或推断其他版本安全；Agent旧字段仍可能partial |
| 前端 | 使用现有入口；需队友按契约展示降级模式、索引状态、时效筛选和观察缺口 |
| Agent | 审查中英文主题约束与词表召回边界，可使用normalized富化，但引用仍以原始版本为准 |
| 完整灾备/容量 | 主机、凭据、模型缓存整体灾备和大并发容量未演练，本轮只完成独立数据/索引恢复与小规模测试 |

运行、停止、日志、观察、恢复命令见 [运行手册](OPERATIONS_20261009.md)。问答逐题与评分定义见 `D:\cit\ICT-team\docs\qa_acceptance_20261009.md`。未经人工复核的评测标注不是人工金标准；观察未结束不得报告48小时通过。

## 修改文件位置

- 后端：`D:\cit\后端开发\backend\services\observation.py`、`enrichment.py`、`agent_gateway.py`；`backend\api\v1\operations.py`、`documents.py`；`backend\tasks\jobs.py`、`celery_app.py`；`backend\models\entities.py`、`__init__.py`；`migrations\versions\0007_runtime_observations.py`。
- 后端回归：`D:\cit\后端开发\tests\test_reliability_upgrade.py`；契约生成及运行/观察/恢复/性能/富化验证脚本位于同工程`scripts`，结果位于被忽略的`output`。
- Agent：`D:\cit\ICT-team\packages\rag_agent\src\rag_agent\retrieval\hybrid.py`；`tests\test_qa_relevance.py`、`test_windows_rollback_guard.py`；两个评测fixture。
- Agent配套：`D:\cit\ICT-team\scripts\evaluate_qa_acceptance.py`、`evaluate_qa_relevance.py`、`verify_qa_relevance_windows.py`、`validate_qa_runtime_windows.py`、`verify_index_recovery.py`、`audit_public_qa_payload.py`、`run_fullstack_windows.py`；新说明`docs\qa_acceptance_20261009.md`。
- 旧回退镜像实际检查因迁移版本不符被保护拒绝，未停止服务、未切换镜像。回退保护有2项新增测试，避免旧代码误用于0007数据库。
