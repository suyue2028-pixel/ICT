# 已读参考与实际取舍

本次实际读取的文件、固定 commit、URL 与 SHA-256 在 `reference_manifest.json`。参考源码临时下载到 /tmp，只借鉴结构和故障处理思路，本实现独立编写。

## CVE-Vulnerability-Intelligence-Agent

固定 commit `7fe5e059210fe8f0533457266f012f8a30f58d4e`，已读取 app/agent/graph.py、nodes/query_parser.py、retriever.py、formatter.py、services/nvd_client.py。

- graph.py 是固定线性流程；本实现使用 assess_evidence 的条件分支和最多一次 rewrite。
- query_parser.py 能抽 CVE，但 retriever.py 没使用该数组做精确匹配；本实现编号只查结构化 identifiers，未命中单独报告。
- retriever.py 在 filtered 为空时退回 candidates；本实现所有检索/扩展都使用同一硬过滤，不退回宽候选。
- query_parser 的年份模式可能把 CVE 年份当日期；本实现不从编号年份推断发布时间。
- nvd_client 只选 v3.1/v3.0/v2 之一，并将 CPE 简化为 criteria 字符串；本实现保留各版本/来源分数和完整配置条件、边界。
- formatter.py 根据严重性生成通用修复建议；本实现 fixed_version/mitigation 必须有记录证据，否则 unknown。
- fetch_cves 的429分支可能一直 continue；本实现采样最多2次尝试，查询共享总调用预算。

规格和原项目文件树未发现 LICENSE。本次没有复制其实现，不将其代码作为可复用组件。

来源：[指定版本](https://github.com/ThejVaibhav/CVE-Vulnerability-Intelligence-Agent/tree/7fe5e059210fe8f0533457266f012f8a30f58d4e)。未运行其 Weaviate/远程模型系统。

## NVIDIA vulnerability-analysis

固定 commit `a524fefee27b16e5a41f8a02f72db2c7985e35d3`，读取 src/vuln_analysis/pipeline/workflow.py、plugins/cve_researcher/agent.py、report_validation.py、data_models/cve_researcher.py 和 LICENSE。核查重点为预算、最终失败路径与校验，未做全面审计或部署。

workflow.py 的阶段失败走 fail_safe，再统一输出；agent.py 显式设置模型/工具预算和 graph recursion limit，并对预算耗尽/递归耗尽产生带状态的 best-effort 报告。report_validation.py 检查版本范围不能包含固定版本、未解析占位符和指令泄漏；数据模型区分 validation/error/termination status。

本实现采用有限失败回退和来源硬事实校验：默认30秒/3次LLM/8次工具/1次改写，非法引用回退有来源摘录；不借入其容器、资产扫描、多研究 Agent 或工具链。原项目 Apache-2.0；未复制专属代码，若后续复用应保留许可、版权和变更说明。

来源：[指定版本](https://github.com/NVIDIA-AI-Blueprints/vulnerability-analysis/tree/a524fefee27b16e5a41f8a02f72db2c7985e35d3)。

## 官方接口与模型

- [LangGraph 当前 Agentic RAG 教程](https://docs.langchain.com/oss/python/langgraph/agentic-rag)：已读 StateGraph、条件边、文档作为不可信数据、rewrite/generate 编排。未使用旧归档 Notebook 或它的旧依赖。实际安装版本在 requirements.lock，已运行 graph.ainvoke。
- [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)：本次通过 HF 元信息确认 revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`。已实际下载并在 CPU 编码为384维归一化向量，query:/passage: 前缀与 tokenizer 分块在代码中约束。
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)：核查 response_format=json_object、JSON 指令和输出结构。适配器使用 httpx、有限重试、共享预算；无密钥，未实调远程接口。HTTP MockTransport 测试只证明请求/异常处理适配，不证明 provider 当前账号可用。

网络浏览工具对部分 GitHub raw 页面返回 cache miss，改由 HTTPS 读取指定 commit，文件下载全部成功；NIST 部分采样页面阻塞/404以及被拒绝的 CVE 记录保留在 demo manifest，未造资料补齐。
