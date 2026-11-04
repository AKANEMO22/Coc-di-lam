"""CLI audit tool for IT Job Sync Service."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add project root to path if executed directly
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.job_sync.database import JobDatabase
from app.job_sync.sync_engine import JobSyncEngine


def main():
    parser = argparse.ArgumentParser(description="IT Job Sync Service CLI Audit Tool")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # Command: sync
    sync_p = subparsers.add_parser("sync", help="Run synchronization")
    sync_p.add_argument("--source", default="all", help="Source to sync: all, topcv, itviec, topdev, vietnamworks")
    sync_p.add_argument("--limit", type=int, default=30, help="Max jobs per source")

    # Command: list
    list_p = subparsers.add_parser("list", help="List synced IT jobs")
    list_p.add_argument("--query", default=None, help="Search query")
    list_p.add_argument("--source", default=None, help="Filter source")
    list_p.add_argument("--limit", type=int, default=10, help="Page size")

    # Command: stats
    subparsers.add_parser("stats", help="Display storage stats and sources")

    # Command: serve
    serve_p = subparsers.add_parser("serve", help="Run web audit dashboard server")
    serve_p.add_argument("--port", type=int, default=8050, help="Port to listen on")
    serve_p.add_argument("--host", default="127.0.0.1", help="Host interface")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    db = JobDatabase()
    engine = JobSyncEngine(db)

    if args.command == "sync":
        print(f"[*] Starting sync for source: '{args.source}' (limit={args.limit})...")
        if args.source.lower() == "all":
            results = asyncio.run(engine.sync_all(limit_per_source=args.limit))
            for r in results:
                print(f"  -> [{r.source}] Status: {r.status} | Fetched: {r.total_fetched} | New: {r.new_jobs} | Time: {r.duration_seconds}s")
        else:
            r = asyncio.run(engine.sync_source(args.source, limit=args.limit))
            print(f"  -> [{r.source}] Status: {r.status} | Fetched: {r.total_fetched} | New: {r.new_jobs} | Time: {r.duration_seconds}s")
        print("[+] Sync finished!")

    elif args.command == "list":
        jobs, total = db.query_jobs(query=args.query, source=args.source, page_size=args.limit)
        print(f"[+] Total jobs matching: {total} (showing up to {args.limit})")
        print("-" * 80)
        for idx, j in enumerate(jobs, 1):
            print(f"{idx}. [{j.source}] {j.title}")
            print(f"   Company: {j.company} | Salary: {j.salary} | Level: {j.seniority}")
            print(f"   Skills: {', '.join(j.skills[:6]) if j.skills else 'None'}")
            print(f"   Link: {j.source_url}")
            print("-" * 80)

    elif args.command == "stats":
        stats = db.get_stats()
        print("=== IT JOB SYNC SERVICE STATS ===")
        print(f"Total active IT jobs: {stats['total_jobs']}")
        print("\nJobs by Source:")
        for src, count in stats["by_source"].items():
            print(f"  - {src}: {count}")
        print("\nJobs by Seniority:")
        for lvl, count in stats["by_seniority"].items():
            print(f"  - {lvl}: {count}")
        print("\nJobs by IT Category:")
        for cat, count in stats["by_category"].items():
            print(f"  - {cat}: {count}")

    elif args.command == "serve":
        import uvicorn
        print(f"[+] Starting Web Audit Dashboard at http://{args.host}:{args.port}")
        uvicorn.run("app.job_sync.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
