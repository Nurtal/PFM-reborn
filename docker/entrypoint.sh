#!/bin/bash
# Prepare /data, then run the web server (with the task consumer inside, or
# beside it when PFM_TASKS=separate). If a process dies, exit so Docker restarts us.
set -euo pipefail
cd /app

mkdir -p "$PFM_DATA_DIR/media" "$PFM_DATA_DIR/backups"

# First start on SQLite: begin from the database migrated at build time.
if [ -z "${DATABASE_URL:-}" ] && [ ! -e "$PFM_DATA_DIR/db.sqlite3" ] && [ -e /app/seed.sqlite3 ]; then
  install -m 600 /app/seed.sqlite3 "$PFM_DATA_DIR/db.sqlite3"
fi
python manage.py pfm_init

granian --interface wsgi --host 0.0.0.0 --port 8000 \
  --workers "${PFM_WORKERS:-1}" --blocking-threads "${PFM_THREADS:-4}" --respawn-failed-workers \
  config.wsgi:application &
web=$!

# Background tasks run inside the web process by default (PFM_TASKS=embedded,
# saves ~70 MB). PFM_TASKS=separate starts a dedicated consumer instead.
pids="$web"
if [ "${PFM_TASKS:-embedded}" = "separate" ]; then
  python manage.py run_huey --flush-locks &
  pids="$pids $!"
fi

stopping=0
stop() { kill -TERM $pids 2>/dev/null || true; }
trap 'stopping=1; stop' TERM INT

status=0
# shellcheck disable=SC2086
wait -n $pids || status=$?
stop
wait || true
# A requested stop (docker stop) is a clean exit; anything else is a failure.
[ "$stopping" = 1 ] && exit 0
exit "$status"
