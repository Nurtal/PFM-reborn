#!/bin/bash
# Prepare /data, then run the web server and the task consumer side by side.
# If either process exits, stop the other and exit so Docker restarts us.
set -euo pipefail
cd /app

mkdir -p "$PFM_DATA_DIR/media" "$PFM_DATA_DIR/backups"
python manage.py pfm_init

granian --interface wsgi --host 0.0.0.0 --port 8000 \
  --workers "${PFM_WORKERS:-1}" --blocking-threads "${PFM_THREADS:-4}" --respawn-failed-workers \
  config.wsgi:application &
web=$!

python manage.py run_huey --flush-locks &
worker=$!

stop() { kill -TERM "$web" "$worker" 2>/dev/null || true; }
trap stop TERM INT

status=0
wait -n "$web" "$worker" || status=$?
stop
wait || true
exit "$status"
