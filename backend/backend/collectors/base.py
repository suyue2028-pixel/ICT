from dataclasses import dataclass, field
from typing import Protocol

from backend.schemas.contracts import RawRecord


@dataclass(frozen=True)
class CollectionBatch:
    records: list[RawRecord]
    next_cursor: dict
    more: bool = False
    continuation_delay_seconds: int = 60
    coverage: dict = field(default_factory=dict)


class Collector(Protocol):
    def collect(self, cursor: dict) -> CollectionBatch: ...


class AdapterNotImplemented(NotImplementedError):
    """适配器未注册时显式报错，绝不以演示内容替代真实响应。"""
