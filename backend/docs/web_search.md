# Tavily 通用联网检索（2026-10-12）

## 配置与真实运行边界

使用单一 Tavily 服务，无新增 Python 依赖，沿用 httpx。请求固定为 `POST https://api.tavily.com/search`，使用 Bearer 认证。请求参数与响应字段依据 [Tavily 官方 Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

仅 FastAPI 后端配置：

```sh
export TAVILY_API_KEY_FILE=/ABSOLUTE_PRIVATE_PATH/tavily-api-key
# 或 TAVILY_API_KEY，二者不能同时设置非空。
# 文件为普通文件、0600、不允许符号链接，至多4096字节。
```

完整本机启动器的私密默认位置由根项目配置为 `.secrets/tavily-api-key`。更改配置需要重启后端。不得把此 Key 或文件路径交给 Vue、VITE 环境变量、Agent、同步 worker或Git。

没有可用密钥时返回 `search_not_configured`，不会提供静态模拟搜索。新增搜索测试使用 Mock HTTP 与 Mock DNS；这些测试不是实际 Tavily 检索成功的证明。真实搜索必须用团队可用 Tavily Key 单独验收。

## 内部协议

沿用现有内部情报查询接口及写令牌：

```text
POST /api/v1/internal/intelligence/lookup
X-Agent-Token: <AGENT_WRITE_TOKEN>

{"tool":"web_search","query":"待查的具体安全问题"}
```

`query` 非空、至多2000字符。请求不接受用户提供的目标URL、服务地址、结果数量、Key或代码。返回沿用 `schema_version/status/documents/checked_at/cache_hit/limitation/tool`，文档通过既有 RawRecord → DocumentRevision → KnowledgeEvent 流水线保存、Hash及引用校验。来源非demo，搜索源采集调度保持关闭。

## 来源与正文口径

Tavily 设置 `max_results<=3`、`search_depth=basic`、`include_answer=false`，请求供应商实际返回的 `raw_content` 与发布时间估计。官方域名采用 prefer，允许其他研究来源出现，但不会把随机网页升级为官方漏洞公告。

- 供应商返回正文：`content_origin=provider_raw_content`，`content_completeness=partial`。
- 只返回搜索摘录：`content_origin=provider_search_snippet`，`content_completeness=summary`。
- 两种都标记 `independent_fetch=false`、`full_text_verified=false`。后端没有独立抓取网页，不宣称抓取全文或核验全文。
- 保留原URL、标题、来源Host、问题、检索时间、摘要及正文出处。正文清洗并限制为12000字符，原始供应商结果保存在版本Payload。
- Tavily 日期是供应商对发布时间或更新时间的估计，保存在 `provider_date_estimate`，不伪装成精确原站发布时间。
- 任意网页中的指令均是不可信来源数据，不能改变Agent权限或工具执行。

除实际识别的OWASP/NIST域名资料类别外，其他网页按article保存，Agent映射为blog。即使是官方域名，搜索摘要也不产生新的CVSS、影响版本、补丁或利用状态结构化事实。

## 安全、限额与失败

固定供应商HTTP请求禁止重定向及环境代理。结果URL须为HTTPS、443或默认端口、无嵌入凭据；禁止回环、私有、链路本地、云元数据、多播与保留地址，所有DNS答案须是公网。DNS解析使用最多2个槽并等待至多1秒；无法确认安全的结果丢弃。没有额外的任意URL抓取网络栈。

最多2个搜索并发、3个结果，供应商请求与结果验收共享5秒截止预算；瞬时失败最多重试一次，429及配额耗尽不立即重试。响应上限1MB。缓存默认300秒、最多128项；缓存命中保留原检索时间。供应商返回Key反射不会进入缓存、文档或错误正文。

错误码分开记录：`search_not_configured`、`search_auth_failed`、`search_rate_limited`、`search_timeout`、`search_source_unavailable`、`search_invalid_response`、`search_unsafe_url`、`search_response_too_large`、`search_busy`。这些不是用户登录过期，也不等于已经检索但没有相关资料。

可通过 `TAVILY_TIMEOUT_SECONDS`（1至5）、`TAVILY_MAX_RESULTS`（1至3）、`TAVILY_CACHE_SECONDS`（0至900）进一步收紧限制。供应商没有返回结果与服务失败分别处理；丢弃危险结果时明确写出限制。
