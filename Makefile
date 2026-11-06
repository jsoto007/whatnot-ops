# Local development. Every DB-touching target prints the target host first.
SHELL := /bin/bash
PY := .venv/bin
E2E_PORT ?= 5055

.PHONY: install db-create db-url api web worker check test lint e2e build-web migrate ext-e2e smoke seed-demo

install:
	cd api && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
	cd web && npm install
	cd extension && npm install

db-url:
	@grep -E '^(DATABASE_URL|TEST_DATABASE_URL)=' .env | sed -E 's#://[^@/]*@#://***@#'

db-create: db-url
	@for db in whatnot_ops whatnot_ops_test; do \
	  psql -h localhost -p 5432 -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$$db'" | grep -q 1 || createdb -h localhost -p 5432 $$db; \
	done

migrate: db-url
	cd api && FLASK_APP=wsgi.py $(PY)/flask db upgrade

# Demo data for looking at the app locally (refused in production).
seed-demo: db-url
	cd api && JOBS_INLINE=1 FLASK_APP=wsgi.py $(PY)/flask seed-demo

api: db-url
	cd api && JOBS_INLINE=1 FLASK_APP=wsgi.py $(PY)/flask run --port 5050 --debug

# Background worker (parsing, exports). `make api` runs jobs inline instead.
worker: db-url
	cd api && $(PY)/python -m app.worker

web:
	cd web && npm run dev

lint:
	cd api && $(PY)/ruff check . && $(PY)/ruff format --check .
	cd web && npm run lint && npm run typecheck
	cd extension && npm run lint && npm run typecheck

test: db-url
	cd api && $(PY)/pytest
	cd web && npm test
	cd extension && npm test

# Builds the extension (extension/dist, load unpacked) and runs its Playwright suite
# against the fixture chat page with the test build.
ext-e2e:
	cd extension && npm run build && npm run test:e2e

check: lint test

build-web:
	cd web && npm run build

# Production-like: Flask serves web/dist, Playwright drives the installed Chrome.
e2e: build-web db-url
	cd api && APP_ENV=test FLASK_APP=wsgi.py $(PY)/flask db upgrade
	( cd api && exec env APP_ENV=test RATE_LIMITS=off JOBS_INLINE=1 AI_PROVIDER=fake FLASK_APP=wsgi.py $(PY)/flask run --port $(E2E_PORT) --no-debugger ) > /tmp/sales-ops-e2e-flask.log 2>&1 & \
	  pid=$$!; trap "kill $$pid 2>/dev/null" EXIT; \
	  for i in $$(seq 1 30); do curl -sf http://localhost:$(E2E_PORT)/api/health >/dev/null && break; sleep 0.5; done; \
	  cd web && E2E_BASE_URL=http://localhost:$(E2E_PORT) npx playwright test

# Smoke test a deployed instance: make smoke BASE_URL=https://sales-ops.onrender.com
BASE_URL ?= http://localhost:5055
smoke:
	@curl -fsS "$(BASE_URL)/api/health" | grep -q '"db":true' && echo "health ok"
	@curl -fsS "$(BASE_URL)/" | grep -q '<div id="root">' && echo "spa ok"
	@curl -fsS "$(BASE_URL)/some/client/route" | grep -q '<div id="root">' && echo "spa fallback ok"
	@test "$$(curl -s -o /dev/null -w '%{http_code}' "$(BASE_URL)/api/nope")" = 404 && echo "api 404 ok"
