import json
from pathlib import Path

from backend.collectors.base import CollectionBatch
from backend.schemas.contracts import RawRecord


class OfflineCollector:
    def collect(self, cursor: dict) -> CollectionBatch:
        path = Path(__file__).resolve().parents[1] / "samples" / "offline.json"
        records = [RawRecord.model_validate(item) for item in json.loads(path.read_text("utf-8"))]
        if any(not item.is_demo for item in records):
            raise ValueError("Offline fixture must contain demo records only")
        return CollectionBatch(records, {"fixture_version": 1})
