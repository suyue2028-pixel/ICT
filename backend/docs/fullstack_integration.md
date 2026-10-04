# 三模块接入补充（2026-10-04）

后端运行模式 INDEX_PROVIDER=external，AGENT_SERVICE_URL=http://127.0.0.1:9001。Agent使用三个独立服务令牌；token用途与V1既有契约一致。前端同源代理/api到8010，POST /api/v1/agent/query 使用普通用户Bearer，后端独立保存会话、预算、审计并核对引用，主路径不会再调用旧qa/ask生成。

本轮增加：documents keyword；source_reported_severity/excerpt/source_products/source_aliases 来自已保存原始数据；intelligence产品/来源/日期筛选及删除隔离；tasks is_demo隔离；stats/relations 排除删除资料；显式CORS；转发白名单X-RAG-Answer-Mode/X-RAG-Embedding-Mode/X-RAG-LLM-Calls；session历史id/created_at及同事务user-before-assistant排序。无新增数据表或迁移。

Agent引用结构保持schema_version=1：UUID/content_hash/content/source/locator/score_type，JSON整值核对后恢复原文键序，仍要求原文片段可定位；后端进行独立版本核验。

本机已有47份真实资料（OSV45、NIST1、政策1），47个真实事件已indexed。另有3个虚构样本，1份AI相关样本独立索引。pytest52项通过（真实PostgreSQL/Redis、测试进程internal）；业务external链通过28项实际HTTP检查。没有实际DeepSeek付费调用，本轮默认extractive。启动器与完整步骤见agent-dev scripts/run_fullstack.py及docs/fullstack_local.md。

后续：团队可复用同一服务器DeepSeek密钥启动Agent Flash，不需要前端密钥；生产登录/反向代理/部署、更多采集与人工答案评测另行验收。历史消息未保存生成模式，前端不据此推断live。
