# 大模型 API 本地配置

大模型密钥可以来自本人、团队或学校的 API 账号，只需该账号能调用所选模型并有可用额度。密钥只配置在 Agent 服务端。浏览器访问业务后端，业务后端调用 Agent；前端不需要模型密钥。

默认 `RAG_LLM_MODE=extractive`，使用真实本地检索和有来源摘录，不调用远程模型。`live` 模式才会发送问题和选中的资料片段到配置的供应商。2026-10-10 本机已通过业务后端完成真实 DeepSeek 调用，配置模型为 `deepseek-flash`；当日最后一次现有漏洞问答为 `live`，生成与核验共 2 次模型请求、约 5.15 秒。被拦截的版本推断导致结果为 `partial`，不能把供应商接通等同于回答质量全部达标。详情见 [answer_quality.md](answer_quality.md)。

## 已支持的接口

适配器使用 Chat Completions JSON 请求：`POST {RAG_LLM_BASE_URL}/chat/completions`，Bearer 密钥，`response_format={"type":"json_object"}`。默认配置为 DeepSeek；其他服务需支持相同请求和响应格式，设置 base URL 和模型名后仍需实际验证。

默认 base URL 为 `https://api.deepseek.com`，模型为 `deepseek-flash`。JSON 输出要求在提示中明确 JSON，并合理设置生成长度；当前实现已包含这些字段。依据：[DeepSeek 官方 JSON Output 文档](https://api-docs.deepseek.com/guides/json_mode/)，核查日期 2026-10-03。

## 方式一：从本地密钥文件读取

在仓库根目录执行以下命令。输入不回显，保存为被 Git 忽略的文件：

```sh
.venv/bin/python - <<'PY'
import os
from getpass import getpass
from pathlib import Path

key = getpass("请输入大模型 API 密钥（不回显）：").strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit("密钥应为单行非空文本")
folder = Path(".secrets")
folder.mkdir(exist_ok=True, mode=0o700)
path = folder / "llm-api-key"
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    handle.write(key)
path.chmod(0o600)
print("密钥已保存在本地，内容未显示")
PY
```

启动 Agent 前设置：

```sh
unset RAG_LLM_KEY
export RAG_LLM_KEY_FILE="$PWD/.secrets/llm-api-key"
export RAG_LLM_BASE_URL=https://api.deepseek.com
export RAG_LLM_MODEL=deepseek-flash
export RAG_LLM_MODE=live
```

本机全栈已经运行时，默认私密密钥文件存在即可只重启 Agent：

```sh
.venv/bin/python scripts/run_fullstack.py restart-agent --live
```

该命令的 readiness 只说明配置和检索模型已加载；实际问答的 `X-RAG-Answer-Mode`、`X-RAG-LLM-Calls` 与 `partial_errors` 才能区分真实生成、生成失败后的摘录和 mock。已有聊天不会自动重新生成，需要发送一条新问题。

## 方式二：环境变量

可以通过终端或部署环境设置 `RAG_LLM_KEY`，同时取消 `RAG_LLM_KEY_FILE`。两种密钥方式不能同时配置。不要把真实密钥写进 README、`.env.example`、截图或聊天记录。

包不会自动加载 `.env` 文件；`.env.example` 是配置清单。运行进程需要接收到实际环境变量。服务启动后修改配置需要重启进程。

## Flash 请求适配

官方 DeepSeek HTTPS 接口发送 `thinking={"type":"disabled"}`，兼容服务不会自动收到该供应商参数。`finish_reason=length`、其他中断、空内容或非JSON对象不会被当成成功回答。历史 HTTP 测试替身记录与 2026-10-10 的真实问答演示分别保留；本轮没有运行回归测试。全栈启动方法见 [fullstack_local.md](fullstack_local.md)。

## 预算与失败行为

- 总请求预算默认 30 秒，V1 后端适配层上限 25 秒，为后端默认 30 秒网关留出余量。
- 最多 3 次模型 HTTP 请求，重试计入总预算；只有 429/503 会做一次有限重试。
- live 路由和无结果后的术语改写不消耗模型请求；常规问题为相关性筛选、生成、核验预留预算，编号问题通常只需生成与核验。重试不得耗尽预留的核验次数；核验预算不足时明确退回摘录。
- `synthesize` 的供应商 `max_tokens` 不超过后端传入的回答预算。最终 V1 正文还受保守 UTF-8 字节预算限制；这是长度控制规则，不是供应商 tokenizer 的精确计量。
- 最多 8 次本地工具调用，后端给出更小上限时采用更小值。
- 供应商响应异常、无效 JSON 或无效引用会降级为摘录，或者因预算/超时返回明确错误。响应中的模式标记与实际生成方式一致。
- 不公开供应商错误正文；密钥使用 `SecretStr`，密钥文件须是至多 16 KB 的普通文件。HTTP 关闭重定向和环境代理。

用户使用的 `RAG_LLM_KEY` 与三个模块互相认证的 `RAG_SERVICE_TOKEN`、`RAG_BACKEND_TOOL_TOKEN`、`RAG_BACKEND_WRITE_TOKEN` 用途不同，不能混用。
