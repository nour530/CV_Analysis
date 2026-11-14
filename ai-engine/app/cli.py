from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence

from app.api.settings import get_api_settings
from app.core.logging import (
    configure_security_logging,
    install_request_id_log_attribute,
)
from app.db.session import get_session_factory
from app.services.jobs.sources import (
    build_http_client,
    build_sources,
    load_source_configs,
)
from app.services.jobs.sync import sync_jobs
from app.services.session_cleanup import cleanup_sessions


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobmatch")
    subcommands = parser.add_subparsers(dest="command", required=True)

    cleanup = subcommands.add_parser(
        "cleanup-sessions",
        help="Delete expired/revoked sessions and stale refresh tokens.",
    )
    cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without changing the database.",
    )
    cleanup.add_argument(
        "--session-retention-seconds",
        type=int,
        default=None,
        help="Override SESSION_CLEANUP_RETENTION_SECONDS for this run.",
    )
    cleanup.add_argument(
        "--refresh-token-retention-seconds",
        type=int,
        default=None,
        help="Override REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS for this run.",
    )

    jobs = subcommands.add_parser(
        "sync-jobs",
        help="Fetch real job postings from the configured job-board APIs.",
    )
    jobs.add_argument("--dry-run", action="store_true", help="Fetch without saving.")
    jobs.add_argument(
        "--sources",
        default=None,
        help="Path to the job sources JSON file (default: JOB_SOURCES_PATH).",
    )
    jobs.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="TYPE[:COMPANY]",
        help="Sync only matching sources, e.g. --only lever or --only smartrecruiters:LEONI1.",
    )
    jobs.add_argument(
        "--closed-retention-days",
        type=int,
        default=30,
        help="Delete postings closed for longer than this many days.",
    )
    jobs.add_argument(
        "--refresh-skills",
        action="store_true",
        help="Re-extract skills for unchanged postings (after a dictionary update).",
    )
    jobs.add_argument(
        "--loop-seconds",
        type=int,
        default=0,
        help="Repeat the sync every N seconds (0 = run once). Minimum 900.",
    )
    return parser


def _run_job_sync(args: argparse.Namespace) -> int:
    settings = get_api_settings()
    try:
        configs = load_source_configs(args.sources or settings.job_sources_path)
    except Exception as exc:
        print(f"Could not load job sources: {exc}", file=sys.stderr)
        return 2
    if args.only:
        wanted = [item.casefold() for item in args.only]
        configs = [
            config
            for config in configs
            if any(
                key == config.type
                or key == f"{config.type}:{(config.company or '').casefold()}"
                for key in wanted
            )
        ]
    if args.loop_seconds and args.loop_seconds < 900:
        print("--loop-seconds must be 0 or at least 900.", file=sys.stderr)
        return 2

    exit_code = 0
    while True:
        with build_http_client() as client, get_session_factory()() as db:
            try:
                result = sync_jobs(
                    db,
                    build_sources(configs, client),
                    closed_retention_days=args.closed_retention_days,
                    dry_run=args.dry_run,
                    refresh_skills=args.refresh_skills,
                )
            except Exception as exc:
                print(f"Job sync failed: {type(exc).__name__}", file=sys.stderr)
                exit_code = 1
            else:
                print(json.dumps(result.to_payload(), sort_keys=True), flush=True)
                exit_code = 0 if all(not s.error for s in result.sources) else 3
        if not args.loop_seconds:
            return exit_code
        time.sleep(args.loop_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "sync-jobs":
        install_request_id_log_attribute()
        configure_security_logging()
        return _run_job_sync(args)
    if args.command != "cleanup-sessions":
        return 2

    install_request_id_log_attribute()
    configure_security_logging()
    settings = get_api_settings()
    session_retention = (
        args.session_retention_seconds
        if args.session_retention_seconds is not None
        else settings.session_cleanup_retention_seconds
    )
    token_retention = (
        args.refresh_token_retention_seconds
        if args.refresh_token_retention_seconds is not None
        else settings.refresh_token_cleanup_retention_seconds
    )
    if session_retention < 0 or token_retention < 0:
        print(
            "Retention seconds must be zero or greater.",
            file=sys.stderr,
        )
        return 2

    try:
        with get_session_factory()() as db:
            result = cleanup_sessions(
                db,
                session_retention_seconds=session_retention,
                refresh_token_retention_seconds=token_retention,
                dry_run=args.dry_run,
            )
    except Exception as exc:
        print(
            f"Session cleanup failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result.to_payload(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
