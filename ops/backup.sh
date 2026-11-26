#!/usr/bin/env bash
# PostgreSQL backups for JobMatch AI, run by the `backup` service in docker-compose.yml.
#
#   backup.sh once          take one backup now, verify it, prune old ones
#   backup.sh loop          the same every BACKUP_INTERVAL_SECONDS (default 86400)
#   backup.sh restore-test  restore the newest backup into a scratch database,
#                           compare its row counts with the live database, drop it
#
# Needs PGHOST, PGUSER, PGDATABASE and PGPASSWORD (libpq reads them). Backups go to
# BACKUP_DIR (default /backups) as jobmatch-<UTC time>.dump, in pg_dump's custom
# format. Those older than BACKUP_RETENTION_DAYS (default 14) are deleted.
# A backup counts once it has been restored: run restore-test after setup and on a
# schedule (see README, "Backups").
set -euo pipefail

: "${PGHOST:?PGHOST is not set}" "${PGUSER:?PGUSER is not set}"
: "${PGDATABASE:?PGDATABASE is not set}" "${PGPASSWORD:?PGPASSWORD is not set}"
backup_dir="${BACKUP_DIR:-/backups}"
keep_days="${BACKUP_RETENTION_DAYS:-14}"
interval="${BACKUP_INTERVAL_SECONDS:-86400}"
scratch_db="jobmatch_restore_test"
checked_tables=(users cv_analyses cv_documents job_applications chat_messages)

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

backup_once() {
  mkdir -p -- "${backup_dir:?}"
  local stamp partial final
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  partial="${backup_dir:?}/.jobmatch-${stamp}.dump.partial"
  final="${backup_dir:?}/jobmatch-${stamp}.dump"
  # Each step is checked explicitly: `set -e` does not apply inside a function
  # called from `backup_once || ...` (the loop below), so a failure must not fall
  # through to the rename and leave a broken file looking like a backup.
  if ! pg_dump --format=custom --no-owner --file="$partial"; then
    rm -f -- "$partial"
    log "FAILED: pg_dump" >&2
    return 1
  fi
  # A dump that pg_restore cannot list is not a backup: fail before it replaces anything.
  if ! pg_restore --list "$partial" >/dev/null; then
    rm -f -- "$partial"
    log "FAILED: the dump is unreadable" >&2
    return 1
  fi
  mv -- "$partial" "$final" || return 1
  log "ok ${final} ($(du -h -- "$final" | cut -f1))"
  find "${backup_dir:?}" -maxdepth 1 -type f -name 'jobmatch-*.dump' -mtime +"$keep_days" \
    -print -exec rm -f -- {} +
}

restore_test() {
  local latest
  # Backup names contain no spaces or newlines, so ls output is safe to read here.
  latest="$(ls -1t -- "${backup_dir:?}"/jobmatch-*.dump 2>/dev/null | head -n 1 || true)"
  if [[ -z "$latest" ]]; then
    log "restore-test FAILED: no backup in ${backup_dir}" >&2
    exit 1
  fi
  dropdb --if-exists -- "$scratch_db"
  createdb -- "$scratch_db"
  trap 'dropdb --if-exists -- "$scratch_db"' EXIT
  pg_restore --no-owner --exit-on-error --dbname="$scratch_db" -- "$latest"
  local table live restored
  for table in "${checked_tables[@]}"; do
    live="$(psql -X -tA -c "select count(*) from ${table}")"
    restored="$(psql -X -tA -d "$scratch_db" -c "select count(*) from ${table}")"
    log "${table}: live=${live} restored=${restored}"
  done
  log "restore-test ok from ${latest} (counts can differ by what changed since the backup)"
}

case "${1:-}" in
  once) backup_once ;;
  loop)
    while true; do
      backup_once || log "FAILED (will retry in ${interval}s)" >&2
      sleep "$interval"
    done
    ;;
  restore-test) restore_test ;;
  *)
    echo "usage: backup.sh once | loop | restore-test" >&2
    exit 2
    ;;
esac
