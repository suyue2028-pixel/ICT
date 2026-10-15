"""Time filters and source observations. No publication time is manufactured."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, func

from backend.models import DataSource, Document, KnowledgeEvent, TaskRun

DELIVERY_TARGET_SECONDS = 4 * 60 * 60
DAILY_CACHE_ADAPTERS = {"arxiv"}
DEFAULT_INTERVALS = {
    "osv": 3600, "nvd": 3600, "cisco": 3600, "sans": 3600,
    "huggingface": 3600, "trailofbits": 3600, "nvidia": 3600,
    "crossref": DELIVERY_TARGET_SECONDS, "arxiv": 86400, "nist": DELIVERY_TARGET_SECONDS,
    "govuk": DELIVERY_TARGET_SECONDS, "china_policy": DELIVERY_TARGET_SECONDS,
    "nist_drafts": 3600, "govuk_search": 3600,
}
TIME_COLUMNS = {"published": Document.published_at, "updated": Document.source_updated_at,
                "discovered": Document.first_collected_at, "changed": Document.content_changed_at}


def interval_bounds(adapter):
    """Daily API cache policy is an explicit exception to the four-hour goal."""
    if adapter in DAILY_CACHE_ADAPTERS:
        return 86400, 86400
    return 60, DELIVERY_TARGET_SECONDS


def effective_interval(source):
    """Clamp legacy intervals at execution; retain faster valid schedules."""
    floor, ceiling = interval_bounds(source.adapter)
    return min(ceiling, max(floor, source.interval_seconds))


def source_stale(source, now, last_success=None):
    interval = effective_interval(source)
    grace = max(900, min(3600, interval // 4))
    success = last_success or source.last_success_at
    return success is None or success < now - timedelta(seconds=interval + grace)


def record_failure(source, error_code, now, terminal=False):
    source.consecutive_failures = (source.consecutive_failures or 0) + 1
    source.last_error_code = error_code
    if terminal:
        delay = min(DELIVERY_TARGET_SECONDS, 300 * 2 ** min(source.consecutive_failures, 6))
        source.next_run_at = now + timedelta(seconds=delay)


def apply_time_filter(statement, time_basis="published", recent_hours=None,
                      time_from=None, time_to=None, now=None):
    if time_basis not in TIME_COLUMNS:
        raise HTTPException(422, "invalid_time_basis")
    if any(t is not None and (t.tzinfo is None or t.utcoffset() is None) for t in (time_from, time_to)):
        raise HTTPException(422, "timezone_required")
    if time_from is not None and time_to is not None and time_from > time_to:
        raise HTTPException(422, "invalid_time_range")
    if recent_hours is not None and not 1 <= recent_hours <= 720:
        raise HTTPException(422, "invalid_recent_hours")
    column = TIME_COLUMNS[time_basis]
    if recent_hours is not None:
        now = now or datetime.now(timezone.utc)
        statement = statement.where(column >= now - timedelta(hours=recent_hours), column <= now)
    if time_from is not None:
        statement = statement.where(column >= time_from)
    if time_to is not None:
        statement = statement.where(column <= time_to)
    if any(v is not None for v in (recent_hours, time_from, time_to)):
        # A calendar day/year does not establish that publication was within 48h.
        precision = "date_precision" if time_basis == "published" else "updated_date_precision"
        if time_basis in {"published", "updated"}:
            statement = statement.where(Document.metadata_json[precision].as_integer().is_(None))
    return statement


def source_observation(db, source, now):
    latest = db.scalar(select(TaskRun).where(TaskRun.source_id == source.id, TaskRun.kind.in_(("collect", "offline_demo")))
                       .order_by(TaskRun.created_at.desc()).limit(1))
    active = db.scalar(select(TaskRun).where(TaskRun.source_id == source.id, TaskRun.kind.in_(("collect", "offline_demo")),
        TaskRun.status.in_(("queued", "running"))).limit(1))
    stale = source_stale(source, now)
    if not source.enabled:
        status = "disabled"
    elif active:
        status = "retrying" if active.attempts else "queued"
        if active.status == "running":
            status = "collecting"
    elif source.last_error_code:
        status = "error"
    elif source.last_success_at is None:
        status = "never_checked"
    else:
        status = "stale" if stale else "fresh"
    return {"source_id": source.id, "adapter": source.adapter, "category": source.category,
        "enabled": source.enabled, "interval_seconds": effective_interval(source),
        "configured_interval_seconds": source.interval_seconds,
        "four_hour_schedule_applicable": source.adapter not in DAILY_CACHE_ADAPTERS,
        "schedule_exception": "daily_api_cache" if source.adapter in DAILY_CACHE_ADAPTERS else None,
        "last_attempt_at": source.last_attempt_at, "last_checked_at": source.last_checked_at,
        "last_success_at": source.last_success_at, "last_new_content_at": source.last_new_content_at,
        "next_run_at": source.next_run_at, "status": status, "stale": stale if source.enabled else False,
        "consecutive_failures": source.consecutive_failures, "last_error_code": source.last_error_code,
        "last_task_id": latest.id if latest else None, "last_task_status": latest.status if latest else None,
        "last_task_result": latest.result if latest else None,
        "active_task_id": active.id if active else None}


def recent_counts(db, now, hours=48, ai_relevant=True):
    base = select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))
    if ai_relevant is not None:
        base = base.where(Document.ai_relevant == ai_relevant)

    def count(statement):
        return db.scalar(select(func.count()).select_from(statement.subquery()))

    discovered = apply_time_filter(base, "discovered", hours, now=now)
    return {"window_hours": hours, "window_start": now - timedelta(hours=hours), "window_end": now,
        "ai_relevant": ai_relevant, "newly_discovered": count(discovered),
        "newly_discovered_and_recently_published": count(apply_time_filter(discovered, "published", hours, now=now)),
        "recently_published": count(apply_time_filter(base, "published", hours, now=now)),
        "source_updated": count(apply_time_filter(base, "updated", hours, now=now)),
        "content_changed": count(apply_time_filter(base, "changed", hours, now=now)),
        "definition": "Non-demo current documents; date-only publication excluded; discovery is not publication"}


def freshness_snapshot(db, now, hours=48, ai_relevant=True):
    from math import ceil
    base = select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))
    if ai_relevant is not None:
        base = base.where(Document.ai_relevant == ai_relevant)
    docs = db.scalars(base).all()
    publish_recent, publish_backfill, updates, missing, imprecise, future = [], [], [], 0, 0, 0
    for doc in docs:
        if doc.published_at is None:
            missing += 1
        elif doc.metadata_json.get("date_precision") is not None:
            imprecise += 1
        else:
            delay = (doc.first_collected_at - doc.published_at).total_seconds()
            if delay < 0:
                future += 1
            elif doc.published_at >= now - timedelta(hours=hours):
                publish_recent.append(delay)
            else:
                publish_backfill.append(delay)
        if (doc.content_changed_at and doc.source_updated_at and
                doc.metadata_json.get("updated_date_precision") is None):
            delay = (doc.content_changed_at - doc.source_updated_at).total_seconds()
            if delay >= 0:
                updates.append(delay)

    def distribution(values, target_seconds=None):
        ordered = sorted(values)
        result = {"count": len(ordered), "p50": ordered[ceil(len(ordered) * .5) - 1] if ordered else None,
                "p95": ordered[ceil(len(ordered) * .95) - 1] if ordered else None,
                "max": max(ordered) if ordered else None}
        if target_seconds is not None:
            within = sum(value <= target_seconds for value in ordered)
            result.update(target_seconds=target_seconds, within_target_count=within,
                over_target_count=len(ordered) - within,
                within_target_ratio=within / len(ordered) if ordered else None)
        return result

    indexed = select(KnowledgeEvent.id).where(KnowledgeEvent.document_id == Document.id,
        KnowledgeEvent.content_hash == Document.content_hash, KnowledgeEvent.operation != "delete",
        KnowledgeEvent.status == "indexed").correlate(Document).exists()
    pending_docs = db.scalar(select(func.count()).select_from(base.where(~indexed).subquery()))
    source_rows = [source_observation(db, s, now) for s in db.scalars(select(DataSource)) if s.adapter != "offline"]
    return {"measured_at": now, "recent": recent_counts(db, now, hours, ai_relevant),
        "schedule_policy": {"target_seconds": DELIVERY_TARGET_SECONDS,
            "preserve_faster_schedules": True, "max_failure_backoff_seconds": DELIVERY_TARGET_SECONDS,
            "exceptions": {"arxiv": {"interval_seconds": 86400, "reason": "daily_api_cache"}},
            "delivery_target_verified": False},
        "sources": source_rows, "pending_collection_tasks": db.scalar(select(func.count()).select_from(TaskRun)
            .where(TaskRun.kind == "collect", TaskRun.status.in_(("queued", "running")))),
        "unindexed_current_documents": pending_docs,
        "publish_to_first_collection_seconds": {"recent_publications": distribution(publish_recent, DELIVERY_TARGET_SECONDS),
            "historical_backfill": distribution(publish_backfill), "missing_time": missing,
            "imprecise_time": imprecise, "negative_delay": future},
        "source_update_to_changed_version_seconds": distribution(updates, DELIVERY_TARGET_SECONDS),
        "limitations": ["Historical content/index observation times were not invented",
            "Polling interval is not measured delivery latency; no continuous 48h SLA established",
            "Fixed pages/RSS/search windows cannot guarantee exhaustive publication discovery"]}
