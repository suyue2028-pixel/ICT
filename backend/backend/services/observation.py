"""Actual samples only. Missing samples disclose an unknown/outage interval."""
from datetime import timedelta
import json
from sqlalchemy import select, text, func
from backend.models import RuntimeObservation, RuntimeSample, VerificationProbe, TaskRun, Outbox
from backend.services.freshness import freshness_snapshot


def start_observation(db, now, hours=48):
    db.execute(text("SELECT pg_advisory_xact_lock(7429032)"))
    row = db.scalar(select(RuntimeObservation).where(RuntimeObservation.ends_at > now)
        .order_by(RuntimeObservation.started_at.desc()).limit(1))
    if row is None:
        row = RuntimeObservation(started_at=now, ends_at=now + timedelta(hours=hours), sample_seconds=60)
        db.add(row)
        db.flush()
    return row


def capture_samples(db, now, component_health):
    # SKIP LOCKED + per-bucket uniqueness also protects against duplicate delivery.
    rows = db.scalars(select(RuntimeObservation).where(RuntimeObservation.ends_at >= now)
        .with_for_update(skip_locked=True)).all()
    created = 0
    for row in rows:
        bucket = int((now - row.started_at).total_seconds() // row.sample_seconds)
        if bucket < 0 or db.scalar(select(RuntimeSample.id).where(
                RuntimeSample.observation_id == row.id, RuntimeSample.bucket == bucket)):
            continue
        snapshot = freshness_snapshot(db, now)
        from backend.services.enrichment_quality import monitoring
        quality = monitoring(db)
        snapshot["enrichment_quality"] = {key: quality[key] for key in
            ("scope", "scanned_documents", "scan_complete", "dimensions", "automatic_checks")}
        heartbeat = db.get(VerificationProbe, "beat-heartbeat")
        snapshot.update(components=component_health,
            beat_heartbeat_count=heartbeat.count if heartbeat else None,
            beat_heartbeat_at=heartbeat.updated_at if heartbeat else None,
            collection_states=dict(db.execute(select(TaskRun.status, func.count()).where(
                TaskRun.created_at >= row.started_at, TaskRun.kind == "collect").group_by(TaskRun.status)).all()),
            outbox_pending=db.scalar(select(func.count()).select_from(Outbox).where(Outbox.sent_at.is_(None))))
        # Enforce serialization here, so secrets/custom exception objects cannot leak through repr.
        detail = json.loads(json.dumps(snapshot, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)))
        db.add(RuntimeSample(observation_id=row.id, bucket=bucket, measured_at=now, detail=detail))
        created += 1
    db.flush()
    return created


def observation_summary(row, samples, now):
    until = min(now, row.ends_at)
    tolerance = row.sample_seconds * 2.5
    gaps, previous = [], row.started_at
    for sample in samples:
        if (sample.measured_at - previous).total_seconds() > tolerance:
            gaps.append({"from": previous, "to": sample.measured_at,
                         "seconds": (sample.measured_at - previous).total_seconds()})
        previous = sample.measured_at
    if (until - previous).total_seconds() > tolerance:
        gaps.append({"from": previous, "to": until, "seconds": (until - previous).total_seconds()})
    down = sum(any(value is False for value in s.detail.get("components", {}).values()) for s in samples)
    done = now >= row.ends_at
    return {"observation_id": row.id, "started_at": row.started_at, "ends_at": row.ends_at,
        "measured_until": until, "sample_seconds": row.sample_seconds, "samples": len(samples),
        "status": ("finished_with_gaps_or_failures" if gaps or down or not samples else "finished_pending_review") if done else "observing",
        "elapsed_seconds": max(0, (until - row.started_at).total_seconds()),
        "gaps": gaps, "gap_seconds": sum(g["seconds"] for g in gaps), "component_failure_samples": down,
        "latest": samples[-1].detail if samples else None,
        "limits": "Missing intervals are unknown, not healthy. End of window is not automatic SLA approval; review sources, failures and latency."}
