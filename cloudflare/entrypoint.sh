#!/bin/bash
# Container entrypoint (Dockerfile). Order matters:
#   1. migrate first, so the job worker never runs against an old schema
#      (on Render the worker boots before the web service migrates);
#   2. start the job worker and gunicorn side by side;
#   3. on SIGTERM (sleep, redeploy) pass it to both and wait, so the worker
#      finishes its current job: Cloudflare waits up to 15 minutes before SIGKILL.
# If either process dies, stop the other and exit, so the platform restarts us.
set -euo pipefail
cd /app/api

: "${DATABASE_URL:?DATABASE_URL is not set (wrangler secret put DATABASE_URL)}"
: "${SECRET_KEY:?SECRET_KEY is not set (wrangler secret put SECRET_KEY)}"

flask --app wsgi.py db upgrade

python -m app.worker &
worker=$!
gunicorn wsgi:app \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers "${WEB_WORKERS:-2}" --threads 4 --timeout 120 \
  --access-logfile - &
web=$!

stop() { kill -TERM "$web" "$worker" 2>/dev/null || true; }
trap stop TERM INT

status=0
wait -n "$web" "$worker" || status=$?
stop
wait "$web" "$worker" 2>/dev/null || true
exit "$status"
