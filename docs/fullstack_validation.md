# 全栈联调验证记录

日期：2026-10-04。工作目录：`/Users/gshy/Desktop/ICT-RAG`。

## 实际运行

- PostgreSQL 16.15、Redis 7.4.10 项目本地编译；迁移至 0005；Celery worker/beat 实际运行。
- 前端 Vue/Vite → 8010 后端 → 9001 Agent；E5 CPU 实际向量检索，固定模型版本，非 mock embedding。
- 有限公开采集保存 47 份真实资料（OSV 45、NIST 1、国内政策 1），47 个真实知识事件全部索引并 ACK；demo 独立导入 3 份，1 份符合 AI 相关范围。
- 全栈重启后资料、索引、用户会话保留，ready 再次通过；启动/停止只处理本项目服务。
- 回答为 extractive，远程模型调用 0；此次没有实际 DeepSeek 生成验收。

## 检查结果

| 检查 | 结果 | 范围 |
|---|---|---|
| Agent pytest | 72 项通过 | 合成边界、存储、编排、HTTP provider 替身、JSON 原文顺序与公开引用编号 |
| 后端 pytest | 52 项通过 | 真实本机 PostgreSQL/Redis，测试进程 `INDEX_PROVIDER=internal`；运行服务仍为 external |
| TypeScript | `vue-tsc --build` 通过 | 当前前端源码 |
| 前端生产构建 | `vite build` 通过 | 图表包有 >500 KB 构建提示，不阻断构建 |
| 真实全栈 HTTP | 28 / 28 通过 | 健康、真实资料、Vite 代理、租约 ACK/proof、问答/追问、原文与定位、幂等、历史、鉴权、scope 隔离、模式响应头 |
| 浏览器 | 实际操作验证 | 问答、追问、刷新恢复、scope 切换、引用版本详情、列表、图谱、任务 |

全栈输入选用真实 `CVE-2024-8309`；每条引用对照后端保留的 exact content_hash 与原文字符范围。结构化字段完整值先核对，再恢复来源 JSON 键顺序并截取传输片段。字段值、类型或数组顺序不同仍被拒绝。大对象超过接口片段长度时保留可定位的原文前缀，完整版本可在来源详情检查。

## 重跑与证据

```sh
PYTHONPATH=packages/rag_agent/src .venv/bin/python -m pytest packages/rag_agent/tests -q
.venv/bin/python scripts/verify_fullstack.py
```

本机完整检查及查询捕获在 `reports/local/fullstack/validation.json`、`query_captures.json`；八个服务状态在 `processes.json`，日志在 `logs/`。这些机器运行产物被忽略，不含入库提交的密钥。当前用户会话配置单独以 0600 保存，不公开。

此次发现并修复：真实数据 DTO/查询接口差异、CORS 与本地用户授权、JSON 排序/截断造成引用误拒、字段引用括号映射、历史缺时间、同事务消息顺序、跨 scope 会话/图谱响应竞争、详情污染列表及历史引用版本跳转。主链不导入前端原 mock 数据，不以 mock 问答替代服务错误。

## 结论边界

28 项是接口与来源可核验性检查，不是自然语言质量评分。Provider 单元测试使用 MockTransport，不是供应商成功调用。固定来源采集不代表持续多渠道覆盖，source_recorded 不代表独立人工验证。中文 Flash 回答、跨文档推理支持率、复杂 CPE 解析、授权资产匹配、生产用户登录和高并发仍未验收。
