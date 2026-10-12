"""Export, check, query-only dry-run, transactionally import or verify public data."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--snapshot-id")
    export.add_argument("--shard-mib", type=int, default=8)
    for name in ("check", "import", "verify"):
        child = sub.add_parser(name)
        child.add_argument("--input", type=Path, required=True)
        if name == "import":
            group = child.add_mutually_exclusive_group(required=True)
            group.add_argument("--dry-run", action="store_true")
            group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from backend.services.public_snapshot import (SnapshotError, configured_secrets, export_snapshot,
        import_snapshot, plan_import, read_bundle, verify_import)
    from backend.core.config import get_settings
    from backend.db.session import get_engine
    from sqlalchemy.orm import Session
    secrets = configured_secrets(get_settings())
    if args.command == "export":
        # Export stays local and ignored until the human-requested upload review.
        output = args.output.resolve()
        if not output.is_relative_to((ROOT / "output").resolve()):
            raise SnapshotError("export_output_must_be_under_ignored_output_directory")
        rejected = []
        bundle = export_snapshot(get_engine(), output, secrets=secrets, snapshot_id=args.snapshot_id,
                                 shard_bytes=args.shard_mib * 1024 * 1024, rejected_records=rejected)
        report_path = output.parent / (output.name + "-rejected-records.json")
        report_path.write_text(json.dumps(rejected, indent=2), encoding="utf-8")
        result = {"snapshot_id": bundle.manifest.snapshot_id, "counts": bundle.manifest.counts,
                  "exclusions": bundle.manifest.exclusions, "output": str(output),
                  "rejected_record_report": str(report_path), "source_database_read_only": True}
    else:
        bundle = read_bundle(args.input, secrets)
        if args.command == "check":
            result = {"snapshot_id": bundle.manifest.snapshot_id, "valid": True, "counts": bundle.manifest.counts}
        else:
            with Session(get_engine()) as db, db.begin():
                if args.command == "verify":
                    result = verify_import(db, bundle)
                elif args.dry_run:
                    result = plan_import(db, bundle)
                else:
                    result = import_snapshot(db, bundle)
                    if not result["verification"]["complete"]:
                        # Tombstones/identity conflicts should not silently produce
                        # a partially successful batch. The whole transaction rolls back.
                        raise SnapshotError("batch_verification_failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "verify" and not result["complete"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        from backend.services.public_snapshot import SnapshotError
        safe = {"error": error.code, "record_or_path": error.record} if isinstance(error, SnapshotError) else {
            "error": "snapshot_operation_failed", "type": type(error).__name__}
        print(json.dumps(safe, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
