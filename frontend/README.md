# AI 安全知识情报系统 · 前端

Vue 3 + Vite + TypeScript 前端，使用 Vue Router、Pinia、Element Plus、Axios、ECharts。当前版本调用小组后端与 Agent 的真实接口；保留浅色仪表盘风格。

## 启动

Node.js 版本要求见 package.json：^22.18.0 或 >=24.12.0。进入本仓库 frontend 目录：

```powershell
npm.cmd ci
npm.cmd run type-check
npm.cmd run build
npm.cmd run dev
```

打开 http://127.0.0.1:5173/。也可使用 start-dev.cmd 启动已安装依赖的前端。5173 使用固定端口，已有开发服务占用时需先关闭重复服务。

本目录只启动前端。后端、数据库和 Agent 需按各模块说明独立启动，未在本目录增加或修改队友服务。当前小组模块分别在 codex/frontend-demo、feature/backend-integration、agent-dev 分支；克隆默认分支不等于取得完整系统。

## 接口与本地配置

VITE_API_BASE_URL 沿用统一 API 模块，推荐 /api/v1；可复制 .env.example 为 .env.local。开发服务器把 /api 代理到后端，默认 http://127.0.0.1:8010。后端地址变化时在启动终端设置：

```powershell
$env:ICT_BACKEND_ORIGIN = 'http://127.0.0.1:8010'
npm.cmd run dev
```

问答需要后端签发的普通短期用户令牌。开发时通过 ICT_LOCAL_SESSION_FILE 指定本机 session JSON 文件（字段 access_token、expires_at、is_demo）；该文件必须由你们的后端认证流程签发，不能提交仓库。生产环境需接入约定的用户登录流程或填写普通短期用户令牌。

```powershell
$env:ICT_LOCAL_SESSION_FILE = 'C:\path\to\user-session.json'
```

顶部本地运行检查可额外配置：

```powershell
$env:ICT_LOCAL_AGENT_ENV_FILE = 'C:\path\to\private-agent.env'
$env:ICT_LOCAL_AGENT_ORIGIN = 'http://127.0.0.1:9011'
```

private-agent.env 使用已有服务凭证 RAG_SERVICE_TOKEN。Agent 在其他本机端口（例如 9001）运行时调整 ICT_LOCAL_AGENT_ORIGIN；省略时默认 9011。上述 ICT_ 配置由启动终端传给开发服务器，仅服务端使用，不是 VITE_ 浏览器变量。不要把管理员、服务或模型密钥写入 VITE_ 环境变量。

Vite 的 /__local/session 和 /__local/runtime 只用于本地开发，校验本机 Host、同源 Origin，并返回 no-store。运行状态桥接访问既有后端 /health/ready 和 Agent /v1/health，服务凭证仅在 Node 进程中读取，运行检查返回内容不包含凭证。未配置检查时显示“未检查”，不会宣称 Agent 已连接。

## 页面与优化内容

| 路径 | 内容 |
| --- | --- |
| / | 指标、发布时间趋势、风险分布、来源统计、最近情报及流程 |
| /intelligence | 搜索、风险筛选、分页；筛选条件保存在 URL |
| /intelligence/{document_id} | 原文、来源、CWE、CVSS 向量、修复版本及结构化字段状态 |
| /graph | 真实来源关系、实体搜索、类别筛选、聚焦与恢复视图 |
| /qa | 多轮问答、回答模式、原文证据与版本引用 |
| /tasks | 后端采集任务、状态、中文结果摘要、耗时及错误 |

- 服务状态分别展示后端、Agent、当前资料范围的索引事件；支持手动检查及每 30 秒自动更新，页面隐藏时暂停定时请求。
- 总览区分来源记录数和已记录的去重 CVE，未知风险使用灰色。同一漏洞可以有多份来源记录，缺少 CVE 的资料不计入 CVE 数量。
- 详情从既有 /documents/{id}/enrichment 读取同一 content_hash 的字段。数值评分缺失时不自行计算，历史引用不混用当前版本结构化信息。
- 问答将原文摘录、版本信息整理成卡片；被截断的 JSON 只展示其中明确出现的字段，保留原始片段及证据核对入口。
- 新回答的模式、警告和来源标题保存在当前浏览器 sessionStorage，刷新后可恢复。旧会话缺少模式记录时显示未知，不补造历史模式。后端仍保存会话正文和引用。
- 图谱按关系证据中的 document_id 区分来源文档与去重漏洞实体，最多加载 30 个漏洞及其来源。默认聚焦选中实体和直接关联；取消聚焦可查看全部已载入节点。图谱代表后端明确来源关联，不代表已完成图谱推理。
- 任务摘要保留可展开的原始结果；“创建至完成”耗时包含排队等待，不能视作纯执行耗时。当前任务页只统计后端返回的采集任务，富化、审核任务接口尚需验收，不提供假执行按钮。

## 演示与限制

后端的真实资料与显式虚构样本分别查询，不使用旧 src/mock 数据替代网络失败。已有 mock 文件保留作为历史资源，没有用它们填充真实接口页面。

建议路线：真实资料总览 → 搜索一条实际漏洞 → 详情核对来源 → 图谱选择实体 → 问答及追问 → 点击引用查看对应版本 → 运行状态。

2026-10-07 本地已验证：45 条 OSV 真实情报、3 条虚构样本隔离；本地 E5 索引及检索；两轮问答和 4 段引用逐段匹配保存原文；问答卡片、刷新恢复模式、详情字段、图谱搜索/类别/空状态、任务摘要及窄屏布局。测试数据属于独立本地数据库，不随前端源码上传，其他成员的数据数量可能不同。

当前验证的回答方式是 extractive（资料摘录），不是远程生成式模型回答；远程 LLM 调用数为 0。远程生成、所有采集来源、独立富化/审核任务、生产部署及并发性能仍待联调，不能写成已完成。

## 构建与部署

type-check 和 build 已通过；ECharts 相关 chunk 约 620 KB，有体积警告，尚未完成拆包优化。构建输出 dist，可通过 npm.cmd run preview -- --host 127.0.0.1 --port 4173 检查静态产物。

2026-10-07 安装后的 npm audit 报告现有锁文件有 6 项告警（5 high、1 critical）。本次保持依赖及锁文件不变，没有执行 audit fix；构建通过不代表依赖安全验收通过，需单独评估升级。

生产环境需配置 SPA 路由回退以及 /api/v1 到真实后端的转发，不能依赖 Vite 开发代理、本地会话路由或运行状态桥接。生产登录、健康状态接口及完整部署方案需与队友确认，本目录不虚构后端服务。

node_modules、dist、本地 .env、会话文件、服务凭证、截图和个人编辑器配置不属于提交内容。
