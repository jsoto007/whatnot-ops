# Sales Ops as one container image, for Cloudflare Containers (cloudflare/).
# Render does not use this file: render.yaml builds on Render's Python runtime.
#
# The container runs the same two processes Render runs as two services:
# gunicorn (the portal + API) and `python -m app.worker` (the jobs table).
# See cloudflare/entrypoint.sh and docs/CLOUDFLARE.md.

# --- 1. Web app and Chrome extension -------------------------------------------
FROM node:22.14.0-slim AS frontend
WORKDIR /src
COPY shared/ shared/
COPY web/package.json web/package-lock.json web/
RUN cd web && npm ci --no-audit --no-fund
COPY extension/package.json extension/package-lock.json extension/
RUN cd extension && npm ci --no-audit --no-fund
COPY web/ web/
COPY extension/ extension/
RUN cd web && npm run build && cd ../extension && npm run build

# --- 2. Python runtime ---------------------------------------------------------
FROM python:3.13.5-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8080
WORKDIR /app
COPY api/requirements.txt api/requirements.txt
RUN pip install -r api/requirements.txt
COPY api/ api/
COPY shared/ shared/
COPY --from=frontend /src/web/dist web/dist
COPY --from=frontend /src/extension/dist extension/dist
COPY cloudflare/entrypoint.sh /usr/local/bin/sales-ops-entrypoint
RUN chmod 0755 /usr/local/bin/sales-ops-entrypoint \
    && useradd --system --uid 10001 --home /app salesops \
    && chown -R salesops /app
USER salesops
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/sales-ops-entrypoint"]
