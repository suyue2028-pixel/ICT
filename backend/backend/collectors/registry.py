from backend.collectors.offline import OfflineCollector

SOURCE_DEFINITIONS = {
    "offline": {"name": "离线演示样本", "category": "offline", "implemented": True},
    "osv": {"name": "OSV AI 软件包漏洞库", "category": "vulnerability", "implemented": True},
    "cisco": {"name": "Cisco PSIRT 厂商公告", "category": "vendor", "implemented": True},
    "sans": {"name": "SANS ISC 安全社区", "category": "community", "implemented": True},
    "nvd": {"name": "NVD AI 相关漏洞", "category": "vulnerability", "implemented": True},
    "arxiv": {"name": "arXiv AI 安全论文摘要", "category": "academic", "implemented": True},
    "trailofbits": {"name": "Trail of Bits 安全分析博客", "category": "blog", "implemented": True},
    "nist": {"name": "NIST AI RMF 官方规范章节", "category": "standard", "implemented": True},
    "govuk": {"name": "英国政府 AI 安全政策与指导", "category": "policy", "implemented": True},
    "crossref": {"name": "Crossref AI 安全论文元数据", "category": "academic", "implemented": True},
    "huggingface": {"name": "Hugging Face AI 社区", "category": "community", "implemented": True},
    "china_policy": {"name": "中国政府网生成式 AI 法规", "category": "policy", "implemented": True},
    "nvidia": {"name": "NVIDIA AI 产品安全公告目录", "category": "vendor", "implemented": True},
}


def get_collector(adapter: str, config: dict | None = None):
    if adapter == "offline":
        return OfflineCollector()
    if adapter in {"osv", "cisco", "sans"}:
        from backend.collectors.live import LiveCollector
        return LiveCollector(adapter, config or {})
    if adapter in {"nvd", "arxiv", "trailofbits", "nist", "govuk", "crossref", "huggingface", "china_policy", "nvidia"}:
        from backend.collectors.extended import ExtendedCollector
        return ExtendedCollector(adapter, config or {})
    raise ValueError("unsupported_adapter")
