# AI 安全知识情报系统 · 前端

Vue 3、TypeScript、Vite、Pinia、Element Plus、Axios 和 ECharts 前端。当前版本使用小组后端接口，真实资料与显式虚构样本分开查询；网络失败不会替换成模拟成功数据。

## 启动

Node.js 要求：^22.18.0 或 >=24.12.0。在本仓库 frontend 目录执行：

```powershell
npm.cmd ci
npm.cmd run type-check
npm.cmd run build
npm.cmd run dev
```

打开 http://127.0.0.1:5173/。start-dev.cmd 可启动已安装依赖的开发服务。该命令只启动前端，后端、数据库和 Agent 按各自模块说明运行。

沿用 VITE_API_BASE_URL，默认示例为 /api/v1；可将 .env.example 复制为 .env.local。Vite 将 /api 代理到 http://127.0.0.1:8010，需要调整时：

```powershell
$env:ICT_BACKEND_ORIGIN = 'http://127.0.0.1:8010'
npm.cmd run dev
```

问答需要后端签发的普通短期用户令牌。开发可设置 ICT_LOCAL_SESSION_FILE 指向本机会话 JSON（access_token、expires_at、is_demo），或使用页面提供的会话配置。会话文件不提交仓库。/__local/session 仅在本地开发启用并校验 Host、Origin；禁止把管理员、Agent 服务或模型密钥放入 VITE_ 变量。

## 页面

| 路径 | 用途 |
| --- | --- |
| / | 首页；四个指标可展开查看记录、严重风险、来源和产品，另有趋势、风险与来源统计 |
| /intelligence | 情报搜索、风险筛选与分页 |
| /intelligence/{document_id} | 情报详情、八类富化字段、关键原文摘句、来源链接和版本证据 |
| /graph | 情报分类；七类来源及来源关系、八类富化筛选，已替换原图谱画布 |
| /qa | 多轮问答、回答模式、来源引用及原文版本追溯 |

运行状态页面已移除，旧 /tasks 地址跳转首页。界面采用黑白风格，保留风险等内容的语义颜色。

来源分为漏洞数据库、厂商安全公告、安全社区、安全技术博客、学术论文、技术标准与规范、政策法规；离线样本单独展示。按后端 source_id/category 归类，已配置来源不等于正在采集；没有记录的来源显示零。

富化分为受影响产品与版本、CWE、CVSS、修复信息、技术分析、公开 POC 元数据、关联论文、公开利用状态。字段与 document_id/content_hash 对应，不混入历史版本；缺失显示暂无数据，模型结果保留待审核标记。POC 只展示元数据，不执行。论文展示题目、关键证据句和原文链接，不展示全文。来源关系展示分类、来源、记录、CVE 和原文链接，不声称进行了关系推理。

建议演示：首页展开指标 → 情报详情查看证据 → 情报分类切换来源与富化 → 问答追问 → 点击引用核对原文。

## 当前验证与限制

2026-10-09 本地集成验证：45 条真实 OSV 来源记录已索引；两轮问答成功，返回 2 个来源、4 段可逐字核对的证据，会话保存 4 条消息。当前是 E5 实时检索和 extractive 资料摘录模式，远程 LLM 调用为零，不能当作生成式模型已验证。测试数据库不随前端上传，其他成员的记录数量可能不同。

七类来源表示分类覆盖，不表示每类均有采集数据。当前本地论文、POC 和公开利用状态没有实际富化记录；不能把接口支持写成已完成自动富化。生产登录、所有来源采集、自动论文关联、公开利用状态富化、远程生成模型和部署性能仍需联调。

## 接口联调

继续使用后端现有 /api/v1 接口，具体参数以各模块 OpenAPI 为准：

- /sources：source_id、名称、category、enabled；确认七类来源值和离线样本标记。
- /documents：is_demo、page、page_size 等查询参数；确认筛选、总数、document_id、source_id、external_id、CVE、发布日期和风险字段。
- /documents/{id}：正文、原文 URL、版本 content_hash 与 revisions；引用必须能定位保存版本。
- /documents/{id}/enrichment：document_id、content_hash、source、external；八类字段的 state/value/evidence_id，以及 extraction_method、review_state、证据 locator/excerpt。
- /agent/query：query、session_id、is_demo；回答、sources、evidence、回答模式及错误信息。
- /agent/sessions/{id}：历史消息和引用；普通用户令牌签发、过期与生产登录方式需统一。

富化响应结构示意（字段缺失明确标记，不补造值）：

```json
{"document_id":"示例文档ID","content_hash":"对应版本哈希","source":{"cwe":{"state":"unknown","value":null}},"external":{}}
```

## 构建与部署

构建输出 dist。生产服务需要 SPA 路由回退及 /api 到真实后端的反向代理；不能依赖 Vite 本地会话桥接。前端目录不附带虚构后端服务。ECharts 相关首页 chunk 仍有体积警告，后续可优化拆包。

不提交 node_modules、dist、本地 .env、会话文件、密钥、测试截图和个人编辑器配置。
