# Sales Ops — working rules

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
- Ingest auth is a per-device API key, only its SHA-256 is stored
  (`api/app/services/devices.py`). Laptops normally get it by pairing: the
  portal makes a single-use 10-minute code, the extension trades it at
  `POST /api/pair` (conditional UPDATE decides the winner). The portal serves
  the built extension at `/api/extension/download` with its own URL baked in.
- **Admin**: `ADMIN_EMAIL` names the shop's protected owner
  (`services/admin.py`). Only it may sign up, once, and only with the
  one-time `SETUP_TOKEN` (required in production); it cannot be demoted or
  disabled; login restores it. No ADMIN_EMAIL/SETUP_TOKEN in production =
  sign-up closed.
- **Fail closed.** `APP_ENV` defaults to production and must be
  development/test/production; dev conveniences (dev secret, open sign-up,
  links in API responses) only for development/test (`config.is_dev`).
- **Rate limits** live in `services/ratelimit.py` (Postgres counters, own
  transaction): per address for unauthenticated routes and wrong device keys,
  per device for `/api/ingest*`, per user for every signed-in write
  (`auth.require`) and uploads, per shop for "Parse now" and AI audits. Add a
  limit to any new unauthenticated or costly route. `RATE_LIMITS=off` works
  only in dev/test (the browser e2e sets it).
- **Text from outside** (captured chat, uploaded CSVs, the show URL) goes
  through `services/text.py` before it is stored: no control characters or
  invisible / bidi marks, show URLs only on whatnot.com. Store and show it as
  plain text; never render it as HTML.
- **Chat is attacker-controlled.** Anyone watching can type in it: never let
  a viewer line become a sale, a cancel or the current listing without the
  host/platform or a person. The AI may auto-apply only what
  `supervisor._corroborated` ties to a host/platform line. Excel cells are
  always written as text. Security regressions: `api/tests/test_security.py`. Rows are idempotent by
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
  50-thread no-lost-updates test — keep it passing.
- **Overselling is allowed** (Javier's call, 2026-10-20): a reserve never
  fails for lack of stock. `available` goes negative and `Sku.short` is how
  many to buy; Inventory, the dashboard and the Excel sheet show it. Counts
  (adjust) may land below what is held, but never below zero. Only the
  spreadsheet import may carry the sheet's own negative count
  (`allow_negative`, Javier's call 2026-10-24); it shows as `short`.
- **Receiving and counts** live in `services/stock.py` (`receive()`,
  `count()`, `levels()`, `activity()`); both take a client request id so a
  retried form moves nothing twice. Keep the Inventory page's numbers there.
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
  fulfilment, which requires every item paid. Whatnot charges the winner at
  the win, so `pipeline.autopay()` marks every won sale paid at once (orders
  never wait for payment). A decline — read by the extension from Whatnot's
  player / Sold tab (`extension/src/capture/decline.ts`, unverified against a
  real decline), said by the host/platform, or in the Seller Hub CSV — closes
  it `payment_failed` and restocks unless picking began; a person may restore
  it once in Review. A viewer's chat line never pays or declines anything.
- **Order slips** (`services/order_slips.py`, `/app/orders/slips/:showId`) are
  the per-show packing document staff used to build from Whatnot's shipments
  tab: a cover with totals, then one printed page per buyer. Auction Number
  comes from the lot title (`rules.lot_label`); "Gift N" is
  `Sale.gift_count`, set only by a person; giveaways are "Gift" rows and
  count as items.
- **Whatnot shipments** (`services/shipments.py`, `POST /api/ingest/shipments`,
  `extension/src/shipments/`): after a show the host opens Seller Hub →
  Shipments and presses "Send shipments" in the panel; it reads every page of
  the table by column header and the server matches rows to a show by order
  date. Evidence only — never changes a sale, order or stock; only the
  recipient's username is kept (no address). The table was seen empty only:
  confirm the reader against a filled page from the shop's account.
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
