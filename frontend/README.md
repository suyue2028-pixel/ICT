# 前端真实接口联调（2026-10-04）

已接业务后端 documents/sources/tasks/stats/intelligence/relations 与 /agent/query，不导入原 mock 数据来填充页面。真实/虚构范围独立，会话持久保存于后端；引用按 document_id + content_hash 打开对应原文版本。

本机根项目的 `scripts/run_fullstack.py start --detach` 同时启动三模块，页面 http://127.0.0.1:5173。前端独立启动需要 Node 24.12+、npm ci，后端默认 http://127.0.0.1:8010，可设置 ICT_BACKEND_ORIGIN。

用户令牌只在内存中；本地开发可通过 ICT_LOCAL_SESSION_FILE 指定后端签发的普通短期用户session JSON文件（access_token/expires_at）。该文件不能提交。Vite /__local/session 仅校验本机Host和同源Origin，no-store；生产需要后端登录或手动短期用户令牌。不要向前端注入管理员、服务或模型密钥。

问答显示真实响应头/模式说明，模型失败后摘录与无模型摘录分别说明。历史消息尚无持久生成模式，明确显示未知。缺失评分为unknown，未知结构字段不造值，图谱只显示后端记录的来源关联（最多30个漏洞）。

本轮 vue-tsc --build 和 vite build 通过，页面验证问答/追问/刷新恢复/scope/详情/列表/图谱/任务。真实模型生成未验证。整体接入及数据口径见 agent-dev 的 docs/fullstack_local.md 和 docs/fullstack_validation.md。以下为此前前端阶段资料：

# 智能体驱动的 AI 安全知识情报系统 · 前端

Vue 3 + Vite + TypeScript 比赛演示前端，使用 Vue Router、Pinia、Element Plus、Axios 和 ECharts。当前所有业务页面均显示 **演示数据** 标记。

界面参照安全情报仪表盘模板，采用浅灰蓝背景、白色面板与蓝色重点色。顶部包含 AI 渐变文字品牌、情报搜索及演示状态；桌面使用带图标和文字的侧栏导航，手机使用顶部菜单。首页包含四张指标卡、按风险等级展示的发布时间趋势、带数量与占比的风险环图、重点风险情报表格、最近情报、来源统计及处理流程。情报搜索与分页、详情证据、图谱选择、多轮模拟问答和任务阶段筛选沿用原有功能。

首页时间范围提供 7 天、30 天、90 天和 1 年，默认 30 天；以**演示样本最新发布日期**为截止日期，包含截止日，同步筛选指标、图表与首页列表。当前只有 8 条样本，因此不同范围可能显示相同结果。顶部日期是样本日期，不是后端同步时间；未添加模板中的虚构增长率、CISA 数据、账户和通知功能。

## 启动与检查

先确保当前终端能运行 Node.js 和 npm，版本要求见 `package.json` 的 `engines`（Node.js `^22.18.0` 或 `>=24.12.0`）。队友获取代码后，在前端项目目录执行：

```powershell
npm.cmd ci             # 首次安装依赖时执行
npm.cmd run dev
npm.cmd run type-check
npm.cmd run build
```

构建后可运行 `npm.cmd run preview -- --host 127.0.0.1 --port 4173`，在本机打开 `http://127.0.0.1:4173/` 查看构建后的演示页面。情报中心的搜索、风险筛选和页码保存在地址栏参数中，复制该地址可直接打开相同筛选结果。

终端会打印开发地址，通常是 `http://localhost:5173/`。Windows 下也可以在 Node.js/npm 已可用、依赖已安装的终端运行 `.\start-dev.cmd`；脚本会切换到项目目录并检查运行条件，不包含个人电脑的工具路径。生产文件输出到 `dist/`；本仓库没有配置后端 Docker 服务。

## 提交到小组仓库

当前版本应标注为“前端演示版”：页面可交互，但尚未接入真实后端或 RAG。

1. 先克隆小组现有仓库，并从其默认分支创建自己的开发分支。
2. 按小组已有结构，把前端代码放到约定目录（例如 `frontend/`）；已有前端文件时先确认差异，再合并。
3. 提交 `src/`、`public/`、项目配置、`package.json`、`package-lock.json`、`README.md`、`.env.example` 和 `start-dev.cmd`。
4. `node_modules/`、`dist/`、本地环境文件、个人编辑器配置和测试截图不属于交付源码；复制前端到小组仓库时，保留本项目的 `.gitignore`。
5. 在前端目录运行类型检查和构建后，推送开发分支并创建 Pull Request，由小组审核合并。

仓库地址、目标目录、分支和写入权限需与组长确认。小组已有提交历史时，应在克隆后的仓库中提交代码，保持原有历史。

## 演示路径

| 路径 | 内容 |
| --- | --- |
| `/` | 总览：流程、指标、风险分布、趋势、来源、最近情报 |
| `/intelligence` | 搜索、风险筛选、分页 |
| `/intelligence/DEMO-001` | 详情、CVE/CVSS/CWE、受影响产品、建议、来源与证据 |
| `/graph` | 可点击、拖动、缩放的演示实体关系图 |
| `/qa` | 多轮对话界面、本地模拟回答及情报引用 |
| `/tasks` | 采集、富化、审核、问答的演示状态与错误样例 |

建议现场演示路线：**总览 → 情报中心 → DEMO-001 详情 → 知识图谱 → 问答 → 运行状态**。

桌面端将指针停在左侧导航栏的空白区域，可用触摸板横向滑动切换页面：向左下一页、向右上一页。顺序与菜单一致，一次滑动及其惯性事件只切换一页；第一、最后一页不会循环。详情页按“情报中心”所在位置切换。菜单链接、问答入口、正文区域、上下滚动和缩放不参与此手势；滑动停止后才能开始下一次切换。

滑动时会在指针附近显示方向箭头、目标页面和进度；达到切换阈值后显示切换结果，停止滑动后自动消失。惯性回弹不会翻转本次箭头；到达首尾页时显示边界提示。提示不占据页面布局、不拦截点击。

## 模拟数据与限制

- `src/mock/intelligence.ts` 保存全部虚构情报、图谱和任务记录；`src/mock/answers.ts` 是本地关键词匹配回答。记录编号以 `DEMO-` 开头，不代表真实漏洞、采集结果或审核结论。
- 当前前端没有调用采集、富化、审核、RAG、知识图谱或智能体后端。图谱由静态关系生成；任务状态和时间是样例；任务页没有执行按钮。
- 问答不会调用模型或 RAG，回答会标记“模拟回答”。对话只保存在当前页面内，刷新后清空。引用链接指向本地演示情报，并非外部原文。
- 详情字段缺失时展示“暂无数据”。加载、空结果及数据加载错误视图已提供；真实网络错误处理需接口联调后验证。
- `src/api/http.ts` 提供统一 Axios 客户端与泛型请求封装，继续使用 `VITE_API_BASE_URL`（见 `.env.example`）。**接口路径尚未确定，演示页面不会发起 API 请求。** 请不要把模型密钥写入 `VITE_` 环境变量。

## 联调前需要确认的接口契约

下表的路径均为**讨论用候选路径，不表示队友已经实现**。确认后再在 `src/api/` 中增加具体请求函数，并将 Pinia store 的数据源切换到真实接口。需一并确认鉴权、CORS、时间格式、空值、错误响应、分页总数和可公开演示的数据。

| 负责人 | 候选接口 | 请求参数或请求体 | 需确认的响应字段 |
| --- | --- | --- | --- |
| 成员一 | `GET /api/intelligence` | `q`, `severity`, `page`, `page_size`；明确页码从 0 还是 1 开始 | `items[]`, `total`；每条 `id`, `title`, `summary`, `severity`, `published_at`, `source`, `cve`, `cvss`, `cwe`, `products[]`, `recommendation`, `evidence[]`, `tags[]` |
| 成员一 | `GET /api/intelligence/{id}` | 路径参数 `id` | 与列表条目相同的详情字段，特别是证据 `title`, `url`, `excerpt`；404 格式 |
| 成员一 | `GET /api/intelligence/stats` | 可选时间范围 `from`, `to` | `total`, `severity_counts`, `daily_counts`, `source_counts`；统计口径与时区 |
| 成员一 | `GET /api/tasks` | `stage`, `status`, `page`, `page_size` | `items[]`, `total`；每条 `id`, `stage`, `name`, `status`, `started_at`, `finished_at`, `detail`, `error` |
| 成员二 | `GET /api/graph` | 可选 `intelligence_id`, `depth` | `nodes[]` 的 `id`, `name`, `category`, `description`, `intelligence_ids[]`；`links[]` 的 `source`, `target`, `relation` |
| 成员二 | `POST /api/chat` | `conversation_id` 可选，`message`，是否需要历史 `messages[]` | `conversation_id`, `answer`, `citations[]`（至少 `intelligence_id`, `label`, `url` 或证据定位）；是否流式及协议 |
| 成员二 | `GET /api/conversations/{id}`（如支持持久化） | 路径参数 `id` | 消息 `role`, `content`, `created_at`, `citations[]`；若无持久化请明确说明 |

讨论用响应示例，字段名和路径都需要队友确认：

```json
{
  "items": [{
    "id": "example-id",
    "title": "示例标题",
    "summary": "示例摘要",
    "severity": "high",
    "published_at": "2026-09-26T09:30:00+08:00",
    "source": "示例来源",
    "cve": null,
    "cvss": null,
    "cwe": "CWE-778",
    "products": ["示例产品"],
    "recommendation": "示例建议",
    "evidence": [{"title": "示例证据", "url": null, "excerpt": "示例摘录"}],
    "tags": ["审计"]
  }],
  "total": 1
}
```

讨论用图谱和问答响应示例：

```json
{
  "nodes": [{"id": "n1", "name": "示例产品", "category": "产品", "description": "说明", "intelligence_ids": ["example-id"]}],
  "links": [{"source": "n1", "target": "n2", "relation": "影响"}]
}
```

```json
{
  "conversation_id": "example-conversation",
  "answer": "示例回答",
  "citations": [{"intelligence_id": "example-id", "label": "示例标题", "url": null}]
}
```

成员一需提供真实列表、详情、统计和任务接口的 OpenAPI 或示例响应；成员二需提供图谱、问答及引用的数据定义，并说明智能体执行过程是否有可展示接口。两位队友还需确认演示环境地址、跨域设置与可公开使用的测试数据。
