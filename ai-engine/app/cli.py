from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from app.api.settings import get_api_settings
from app.core.logging import (
    configure_security_logging,
    install_request_id_log_attribute,
)
from app.db.session import get_session_factory
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
