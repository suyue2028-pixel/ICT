# 大模型 API 本地配置

大模型密钥可以来自本人、团队或学校的 API 账号，只需该账号能调用所选模型并有可用额度。密钥只配置在 Agent 服务端。浏览器访问业务后端，业务后端调用 Agent；前端不需要模型密钥。

默认 `RAG_LLM_MODE=extractive`，使用真实本地检索和有来源摘录，不调用远程模型。`live` 模式才会发送问题和选中的资料片段到配置的供应商。当前没有对真实供应商进行付费调用验证；已有 HTTP 测试替身验证不能视为真实生成成功。

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

## 方式二：环境变量

可以通过终端或部署环境设置 `RAG_LLM_KEY`，同时取消 `RAG_LLM_KEY_FILE`。两种密钥方式不能同时配置。不要把真实密钥写进 README、`.env.example`、截图或聊天记录。

包不会自动加载 `.env` 文件；`.env.example` 是配置清单。运行进程需要接收到实际环境变量。服务启动后修改配置需要重启进程。

## Flash 请求适配

官方 DeepSeek HTTPS 接口发送 `thinking={"type":"disabled"}`，兼容服务不会自动收到该供应商参数。`finish_reason=length`、其他中断、空内容或非JSON对象不会被当成成功回答。新增回归使用HTTP测试替身；未进行真实付费调用。全栈启动方法见 [fullstack_local.md](fullstack_local.md)。

## 预算与失败行为

- 总请求预算默认 30 秒，V1 后端适配层上限 25 秒，为后端默认 30 秒网关留出余量。
- 最多 3 次模型 HTTP 请求，重试计入总预算；只有 429/503 会做一次有限重试。
- `synthesize` 的供应商 `max_tokens` 不超过后端传入的回答预算。最终 V1 正文还受保守 UTF-8 字节预算限制；这是长度控制规则，不是供应商 tokenizer 的精确计量。
- 最多 8 次本地工具调用，后端给出更小上限时采用更小值。
- 供应商响应异常、无效 JSON 或无效引用会降级为摘录，或者因预算/超时返回明确错误。响应中的模式标记与实际生成方式一致。
- 不公开供应商错误正文；密钥使用 `SecretStr`，密钥文件须是至多 16 KB 的普通文件。HTTP 关闭重定向和环境代理。

用户使用的 `RAG_LLM_KEY` 与三个模块互相认证的 `RAG_SERVICE_TOKEN`、`RAG_BACKEND_TOOL_TOKEN`、`RAG_BACKEND_WRITE_TOKEN` 用途不同，不能混用。
