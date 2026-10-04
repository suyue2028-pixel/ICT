"""Synchronize leased backend events with the authenticated Agent index.

Exit codes: 0 successful/empty one-shot batch; 1 backend or event failure;
2 invalid CLI/configuration or initialization; 130 user interruption.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from rag_agent.integration.backend import BackendClient, BackendError, BackendSettings, BackendSyncWorker
from rag_agent.integration.index import BackendIndex
from rag_agent.service import RAGService


def _emit(data):
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), flush=True)


def _limit(value):
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("limit must be between 1 and 20")
    return number


def _interval(value):
    number = float(value)
    if not 1 <= number <= 300:
        raise argparse.ArgumentTypeError("poll interval must be between 1 and 300 seconds")
    return number


async def _run(args):
    try:
        settings = BackendSettings.from_env()
        service = RAGService(settings.service_settings())
        worker = BackendSyncWorker(BackendClient(settings), BackendIndex(service))
    except Exception:
        _emit({"schema_version": "1", "is_demo": args.is_demo,
               "status": "failed", "error_code": "sync_configuration_or_initialization_failed"})
        return 2
    while True:
        code = 0
        for scope in ([False, True] if args.all_scopes else [args.is_demo]):
            try:
                result = await worker.run_once(is_demo=scope, limit=args.limit)
                result["status"] = "completed" if not result["failed"] else "failed"
                _emit(result)
                code = max(code, 0 if not result["failed"] else 1)
            except BackendError as exc:
                _emit({"schema_version": "1", "is_demo": scope,
                       "status": "failed", "error_code": exc.code})
                code = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                _emit({"schema_version": "1", "is_demo": scope,
                       "status": "failed", "error_code": "sync_batch_failed"})
                code = 1
        if args.once:
            return code
        await asyncio.sleep(args.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Lease backend knowledge events, verify the local index, then ACK.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit; default is bounded-interval polling.")
    scopes = parser.add_mutually_exclusive_group()
    scopes.add_argument("--is-demo", action="store_true", help="Process explicitly demo-scoped documents (default: real scope).")
    scopes.add_argument("--all-scopes", action="store_true", help="Process real and demo batches separately with one loaded model.")
    parser.add_argument("--limit", type=_limit, default=5, help="Maximum claimed events per batch, 1..20 (default: 5).")
    parser.add_argument("--poll-interval", type=_interval, default=5, help="Seconds between batches, 1..300 (default: 5).")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _emit({"schema_version": "1", "is_demo": args.is_demo, "status": "interrupted"})
        return 130


if __name__ == "__main__":
    sys.exit(main())
