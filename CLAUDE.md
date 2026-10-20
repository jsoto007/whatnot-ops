# Whatnot Ops — working rules

Live-show operations app for a Whatnot seller. `docs/PLAN.md` is the phased
build plan and the source of truth for scope and order; each phase is a PR
rebased onto `main`, merged only with Javier's OK. `README.md` has the run
commands.

## Stack

- `api/`: Flask 3 app factory (`app/__init__.py`), Flask-SQLAlchemy 3 on
  SQLAlchemy 2, Flask-Migrate/Alembic (`api/migrations/`), psycopg 3,
  pydantic for request validation, pytest, ruff. Python 3.13
  (`.python-version`). Venv at `api/.venv`.
- `web/`: React 19 + Vite 8 + TypeScript, React Router 7, TanStack Query 5,
  plain CSS with `src/styles/tokens.css` as the only place for colours,
  radii, spacing and motion. Vitest + Testing Library; Playwright against a
  production build served by Flask (`make e2e`, installed Chrome locally).
  No Tailwind.
- `extension/`: Chrome MV3 extension in TypeScript, bundled with esbuild
  (`build.mjs`). Capture is a pure DOM module (`src/capture/`), storage is
  IndexedDB in the background worker (`src/storage/`), uploads go through an
  outbox (`src/upload/`). Vitest in jsdom; Playwright drives the test build
  on `e2e/fixture/chat.html` and starts the real API on the test DB
  (`APP_ENV=test`) for `e2e/ingest.spec.ts`.
- `shared/`: `chat-event-batch.schema.json`, the contract for
  `POST /api/ingest`; both sides validate the same `shared/fixtures/`.
- Web sessions: signed cookie (`wn_session`, 14 days) carrying the user's
  `session_token`, which is rotated on role change, disable, password reset
  and "log out everywhere". Every non-GET `/api` call from the SPA sends the
  double-submit `X-CSRF-Token` header (`web/src/lib/api.ts` fetches
  `/api/auth/csrf` once). Routes declare a permission with
  `@require("…")` (`api/app/auth.py`); the role → permission matrix is only
  in `api/app/services/permissions.py`, and the SPA gets the user's list
  from `/api/auth/me`. Every state change writes `audit_log`
  (`services/audit.py`).
- Ingest auth is a per-device API key (`flask devices create`), only its
  SHA-256 is stored (`api/app/services/devices.py`). Rows are idempotent by
  `(show_id, client_id)`; a batch is all-or-nothing.
- Deploy: Render Blueprint (`render.yaml`), one Python web service that
  builds `web/dist` and serves it through Flask (`api/app/spa.py`). Same
  origin, no CORS.

## Rules

- **Database safety.** `DATABASE_URL` has no default; the app refuses to
  start without it. `make` targets that touch the DB print the host first
  (`make db-url`). Local `.env` points at Postgres.app `localhost:5432`.
  Never run a seed, migration or admin command against a host you have not
  just printed and confirmed.
- **Migrations.** Create with `cd api && FLASK_APP=wsgi.py .venv/bin/flask db
  migrate -m "<name>"`, review the file, then `flask db upgrade`. A migration
  that has run on prod is frozen; schema changes go in a new revision.
- **Money is integer cents. Time is tz-aware UTC** via
  `datetime.now(timezone.utc)`; render in the shop's zone (America/Chicago;
  show date rolls at 5 AM, see `docs/PLAN.md`).
- **Atomic gates.** Inventory reserve/commit/release, single-use codes and
  job claims use `SELECT … FOR UPDATE` (or `SKIP LOCKED` for jobs), never
  count-then-insert. All stock changes go through
  `api/app/services/inventory.py::move()`; nothing writes `Sku.on_hand` or
  `Sku.reserved` directly. Movements with a `source_type`/`source_id` are
  idempotent (unique per SKU + kind); `tests/test_inventory.py` has the
  50-thread oversell test — keep it passing.
- **Secrets** only from env; never printed, logged or committed. `.env` is
  gitignored.
- **Parser.** Classification rules live only in `api/app/services/rules.py`
  (pure, tested against `tests/fixtures/chat_corpus.jsonl`, which is
  synthetic — say so whenever quoting its numbers). State changes to sales
  go through `pipeline.set_sale_status()`; review decisions through
  `pipeline.resolve()`. Both are idempotent on replay.
- **Jobs.** `api/app/services/jobs.py` is the queue (`SELECT … FOR UPDATE
  SKIP LOCKED`); handlers register with `@handler("kind")`. `JOBS_INLINE=1`
  runs them synchronously (dev, e2e, CI); production runs `python -m
  app.worker`.
- **Migrations.** `flask db migrate` prints the generated path — rename
  *that* file (never `ls -t`), and when a downgrade drops tables that use
  Postgres enums, drop the enum types too (see 0004).
- **Orders** derive `open / paid / cancelled` from their sales
  (`services/orders.refresh()`), and only `advance()` moves them through
  fulfilment, which requires every item paid. Payment comes only from
  `services/reconcile.py` (the Seller Hub CSV) or a person in Review; chat
  never marks anything paid.
- **Numbers.** Dashboard tiles and the Excel sheets both read
  `services/dashboard.py`; add a metric there, never inline in a route or
  the workbook builder. Reports are built synchronously with openpyxl
  (`services/reports.py`); move them to a job if a shop outgrows that.
- **AI** only proposes. Providers implement `ai/base.py::AiProvider` and are
  chosen by `AI_PROVIDER` (`claude` via the official `anthropic` SDK with
  `output_config.format` structured output, `jev`, `fake` for tests). What may
  be applied without a person is only `ai/policy.py`; applied changes go
  through `supervisor.apply_proposal()` → the same pipeline/inventory
  functions as a human action, actor `ai`, audited. Never call a provider
  outside `supervisor.ask()` (it logs `ai_calls`). Tests use `FakeProvider`;
  no test may reach a real vendor.
- Commits only when asked, on a feature branch; stage by path.

## Commands

```bash
make install      # api venv + web npm install
make db-create    # local whatnot_ops + whatnot_ops_test
make api          # Flask :5050 (debug; 5000 is macOS AirPlay)
make web          # Vite :5174, proxies /api → :5050
make check        # ruff + pytest, eslint + tsc + vitest
make e2e          # build web, serve via Flask :5055, Playwright
```
