# Agent 本轮实现与验证状态

记录日期：2026-10-04（Asia/Shanghai）。本轮于10月3日开始，交付分支为 `agent-dev`。本记录区分已经执行的独立验证与尚未完成的真实整链验收；提交身份以该分支Git历史为准。

## 已写入的功能

- 独立后端HTTP V1适配与严格模型：health/query/indexes verify、服务Bearer认证、历史转换、请求预算、来源UUID/hash/摘录核对、生成模式标记。
- 后端版本身份映射、真实E5索引证明、当前映射按真实/demo隔离。已完成隔离合成契约资料的实际E5导入及适配器启动。
- 带租约的知识事件同步客户端与CLI：精确读取版本、证明后ACK、读写令牌分离、有限重试、安全错误。
- 检索范围与预期版本过滤、按chunk集合区分矩阵缓存、真实工具事件、当前文档失效与历史证据保留。
- 大模型本地密钥文件、远程响应体积限制、安全错误及供应商生成预算。远程供应商未实调。
- V1 JSON Schema、接入说明和模型配置说明。

## 已完成验证

| 项目 | 结果 | 范围 |
|---|---|---|
| 原有回归 | 54/54通过 | 合成回归；不代表真实远程模型 |
| 真实资料用例复测 | 25/25自动检查通过 | 19篇真实资料、本机真实E5、extractive；LLM调用0 |
| 检索Recall@5 | 0.9649 | 19个可评分问题，非全部问题均有该指标 |
| 引用可定位 | 75/75 | 自动字符区间/JSON定位，人工支持率仍待评审 |
| 新增核心边界手动检查 | 9/9通过 | 合成资料/mock embedding，范围隔离/失效/恢复/预算/工具事件/密钥文件 |
| 本地缺席删除证明检查 | 4/4通过 | 隔离合成身份；实际查SQLite映射/孤立导入，非HTTP联调 |
| 新V1 HTTP契约检查 | 27/27通过 | 实际本机HTTP、合成后端身份/正文、真实E5；按实际后端AgentAnswer模型验收，LLM调用0 |
| 后端原文定位边界 | 3/3通过 | 正文与原revision JSON Unicode字符区间；缺失摘录拒绝 |
| 同步worker手动检查 | 21/21通过 | HTTP全部为MockTransport；7项使用真实SQLite/E5，14项使用受控IndexDouble，后端身份/正文均为合成 |
| V1 schema导出、同步CLI help | 完成 | 代码加载与生成，不等于HTTP或数据库联调 |

详细本机记录在 `reports/local/agent-v1-validation/reference_evaluation.json`、`core_changes.json`、`absence_proof_validation.json` 与 `v1_http_validation.json`。隔离V1服务使用明确标记的SYNTHETIC UUID/revision资料；已完成真实E5导入、服务启动及27项HTTP探测。验证进程已停止，资料没有导入正式 `data/backend-rag.db`。

随代码交付的无密钥汇总为 [reports/agent_v1_validation.json](../reports/agent_v1_validation.json)。原有演示验证仍保存在`reports/validation_summary.json`，两个记录覆盖不同接口与执行日期。

HTTP检查包括缺失/错误认证、health真实索引数量、严格后端回答模型、UUID/hash与摘录一致、精确原文定位、真实工具事件、模式披露、生成预算、工具耗尽429、错误hash证明拒绝、真实/demo范围隔离、错误schema/非布尔scope/空问题422。字段定位使用后端原始正文或原revision JSON实际字符区间，已修复内部RAG指针与后端结构不一致的问题。

worker检查覆盖读写令牌分离、固定origin与精确UUID/hash/scope、claim失败不自动重试、租约过期不ACK、read/ACK有限重试、409不再次改状态、错误proof、真实新增/更新/删除/缺席证明以及删除后历史证据保留。独立记录为`reports/local/agent-v1-validation/worker_manual_validation.json`，脚本执行前后生产源码hash一致。模拟HTTP通过不代表后端实际ACK成功。

## 未完成与阻塞

1. 同步worker尚未针对真实PostgreSQL/Redis后端进行claim→index→HTTP proof→ACK端到端验证，更新、删除、租约过期和故障恢复仍需整链验收。
2. 已补齐“后端资料在首次同步前已删除”的本地缺席证明；每次verify实际检查当前映射和孤立导入，重复删除、错scope、错hash直接检查通过。仍需在真实后端ACK流程中验收。
3. 大模型密钥未配置，真实生成、真实供应商错误行为和人工语义质量未验证。25个真实资料参考由实现方定义，人工引用支持率和跨文档正确率尚待独立评审。
4. 10月4日重新读取本机后端ready，仍返回unavailable，postgres/redis/migration均false。前端真实API接入仍由前后端同学完成。
5. 原先HTTP验证曾受自动审批服务额度故障阻塞，本轮重新申请审批后已获准并完成27项检查，没有通过其他工具绕过审批。

## 进度估算

原实施提示词要求的第一版核心框架与当前后端V1适配代码已经具备，可进入真实联调；尚不能宣称Agent全部任务完成或“只剩后端连接”。Agent仍需真实模型调用及人工质量验收。原设计明确列为后续范围的富化扩展、复杂CPE求值、授权资产匹配和完整比赛部署，并未因为接口代码完成而自动完成。

下一步启动真实后端完成同步与来源核验，再由前端接真实问答；Agent同时配置本人/团队可用API账号并进行实际生成和人工评测。
