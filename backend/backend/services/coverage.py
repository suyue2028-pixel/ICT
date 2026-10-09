"""Observed source coverage and delivery latency, never an Internet recall claim."""
from collections import Counter
from datetime import timedelta
from math import ceil
from sqlalchemy import select
from backend.models import DataSource, Document, TaskRun
from backend.collectors.registry import SOURCE_DEFINITIONS
from backend.services.freshness import source_observation

SCOPES = {
    "osv": "configured PyPI package inventory; package pagination",
    "nvd": "configured keywords; recent modified windows plus historical backfill",
    "nvidia": "all matching AI-product/CVE rows in the current official catalogue; bulletin summaries",
    "nist": "registered AI RMF sections; changes only",
    "nist_drafts": "current open-for-comment feed, AI/security filter; draft summaries, not all final standards",
    "govuk": "registered UK AI cyber security collection and linked policy pages",
    "govuk_search": "two exact-phrase searches, selected policy types; recent window plus 120-day default backfill",
    "china_policy": "one registered regulation; changes only, not new Chinese regulation discovery",
    "arxiv": "three AI/security search phrases; updated-order pages and watermark; abstracts",
    "crossref": "configured AI/security query and indexed-date windows; metadata/abstracts",
    "huggingface": "prompt-injection search, first three pages; discussion excerpts",
    "trailofbits": "publisher's current RSS feed; bounded current feed window",
    "cisco": "publisher's current RSS feed; AI relevance assessed separately",
    "sans": "publisher's current RSS feed; AI relevance assessed separately",
}


def latency(values, target=21600):
    ordered = sorted(values)
    return {"count": len(ordered), "p50_seconds": ordered[ceil(len(ordered) * .5) - 1] if ordered else None,
        "p95_seconds": ordered[ceil(len(ordered) * .95) - 1] if ordered else None,
        "max_seconds": ordered[-1] if ordered else None, "target_seconds": target,
        "within_target": sum(value <= target for value in ordered),
        "over_target": sum(value > target for value in ordered),
        "observed_within_target_ratio": sum(value <= target for value in ordered) / len(ordered) if ordered else None,
        "is_sla_or_recall": False}


def delivery_metrics(documents, now, hours):
    cutoff = now - timedelta(hours=hours)
    recent, backfill, changes = [], [], []
    missing, imprecise, negative = 0, 0, 0
    for doc in documents:
        metadata = doc["metadata_json"] or {}
        published, discovered = doc["published_at"], doc["first_collected_at"]
        if published is None:
            if metadata.get("date_precision") is not None:
                imprecise += 1
            else:
                missing += 1
        elif metadata.get("date_precision") is not None:
            imprecise += 1
        elif discovered <= now:
            delta = (discovered - published).total_seconds()
            if delta < 0:
                negative += 1
            elif cutoff <= published <= now:
                recent.append(delta)
            elif cutoff <= discovered <= now:
                backfill.append(delta)
        changed, updated = doc["content_changed_at"], doc["source_updated_at"]
        if (changed and updated and cutoff <= changed <= now and
                metadata.get("updated_date_precision") is None and changed >= updated):
            changes.append((changed - updated).total_seconds())
    return {"recent_publication_to_first_collection": latency(recent),
        "historical_first_seen_in_window": latency(backfill), "updated_to_changed_in_window": latency(changes),
        "missing_publication_time": missing, "imprecise_publication_time": imprecise,
        "negative_publication_delay": negative,
        "definition": "Current non-demo AI-relevant documents; exact timestamps only; missing external documents unknown"}


def coverage_snapshot(db, now, hours=48):
    sources = list(db.scalars(select(DataSource).where(DataSource.adapter != "offline").order_by(DataSource.adapter)))
    columns = (Document.source_id, Document.external_id, Document.ai_relevant, Document.published_at,
        Document.first_collected_at, Document.source_updated_at, Document.content_changed_at,
        Document.content_completeness, Document.metadata_json)
    documents = list(db.execute(select(*columns).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))).mappings())
    rows = []
    for source in sources:
        docs = [doc for doc in documents if doc["source_id"] == source.id]
        relevant = [doc for doc in docs if doc["ai_relevant"]]
        latest = db.scalar(select(TaskRun).where(TaskRun.source_id == source.id, TaskRun.status == "succeeded",
            TaskRun.result["coverage"]["mode"].as_string().is_not(None))
            .order_by(TaskRun.finished_at.desc()).limit(1))
        observation = latest.result["coverage"] if latest else None
        inventory = None
        if observation and "eligible_ids" in observation:
            ids = set(observation["eligible_ids"])
            stored = ids & {doc["external_id"] for doc in docs}
            inventory = {"snapshot_at": latest.finished_at, "snapshot_hash": observation.get("snapshot_hash"),
                "eligible_in_observed_catalogue": len(ids), "stored_current": len(stored),
                "not_stored": len(ids - stored), "not_stored_preview": sorted(ids - stored)[:20],
                "observed_catalogue_capture_ratio": len(stored) / len(ids) if ids else None,
                "is_independent_recall": False}
        tasks = list(db.scalars(select(TaskRun).where(TaskRun.source_id == source.id,
            TaskRun.created_at >= now - timedelta(hours=hours))))
        waits = [(task.started_at - task.created_at).total_seconds() for task in tasks
                 if task.started_at and task.started_at >= task.created_at]
        outcomes = Counter(task.status for task in tasks)
        row = {**source_observation(db, source, now), "scope": SCOPES.get(source.adapter, "unknown scope"),
            "documents": len(docs), "ai_relevant_documents": len(relevant),
            "content_completeness": dict(Counter(doc["content_completeness"] for doc in relevant)),
            "latest_scope_observation": observation, "catalogue_capture": inventory,
            "delivery": delivery_metrics(relevant, now, hours), "tasks_in_window": dict(outcomes),
            "queue_and_retry_wait": latency(waits), "exhaustive_internet_coverage": False}
        if source.adapter == "osv":
            from backend.collectors.live import SourceConfig
            row["configured_packages"] = SourceConfig.validate_for("osv", source.config).packages
        elif source.adapter == "nvd":
            from backend.collectors.extended import ExtendedConfig
            row["configured_keywords"] = ExtendedConfig.model_validate(source.config).keywords
        rows.append(row)
    relevant_docs = [doc for doc in documents if doc["ai_relevant"]]
    return {"measured_at": now, "window_hours": hours, "sources": rows,
        "categories": {"registered": sorted({v["category"] for k, v in SOURCE_DEFINITIONS.items() if k != "offline"}),
            "enabled": sorted({row["category"] for row in rows if row["enabled"]}),
            "with_current_ai_documents": sorted({row["category"] for row in rows if row["ai_relevant_documents"]})},
        "adapter_counts": {"implemented_real": len(SOURCE_DEFINITIONS) - 1, "configured": len(rows),
            "enabled": sum(row["enabled"] for row in rows),
            "ever_succeeded": sum(row["last_success_at"] is not None for row in rows)},
        "delivery": delivery_metrics(relevant_docs, now, hours), "independent_vulnerability_recall": None,
        "continuous_sla_verified": False,
        "limitations": ["Catalogue capture measures a saved discovery snapshot, not Internet or independent recall",
            "AI relevance is rule-based, not an independent gold label", "Polling success does not imply publication coverage",
            "RSS windows, searches, draft-only feeds and fixed documents have explicit coverage limits",
            "Shutdown/network gaps remain observation gaps; elapsed publication delay is not polling interval"]}
