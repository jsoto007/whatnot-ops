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
- Web sessions: signed cookie (`wn_session`; `__Host-wn_session` in
  production, 14 days) carrying the user's `session_token`, which is rotated on
  role change, disable, password reset and "log out everywhere". The CSRF
  token lives in that signed session (never a plain cookie): every non-GET
  `/api` call from the SPA sends it as `X-CSRF-Token` (`web/src/lib/api.ts`
  fetches `/api/auth/csrf` once, keeps it in memory, refetches once on a CSRF
  403). Routes declare a permission with
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
  (`auth.require`), uploads, costly reads (`@require(…, heavy=True)`) and team
  invites (each sends an email), per shop for "Parse now" and AI audits. Add a limit to any new unauthenticated
  or costly route. Production trusts `PROXY_HOPS` (default 2: Cloudflare, then
  Render) for the client address; the first visitor request logs a WARNING
  "client address check … match/MISMATCH" against Cloudflare's header.
  Password-reset emails are a `reset_mail` job: the worker needs `MAILGUN_*`
  too, and the web process puts the link's base URL in the job. `RATE_LIMITS=off` works
  only in dev/test (the browser e2e sets it).
- **Text from outside** (captured chat, uploaded CSVs, the show URL) goes
  through `services/text.py` before it is stored: no control characters or
  invisible / bidi marks, show URLs only on whatnot.com. Store and show it as
  plain text; never render it as HTML. What staff type (names, notes,
  reasons) is cleaned the same way on assignment (`models/clean.py`).
- **No internal names on screen.** The browser gets words, not codes: the
  activity log is built as sentences by `services/activity.py` (never raw
  `audit_log.details` or action codes), the SPA labels every status, role and
  lot type through `web/src/lib/labels.ts`, the Excel sheets use the same
  words, and errors never name a setting (`ADMIN_EMAIL`, `AI_PROVIDER`…), a
  permission or a vendor's raw error text. Stock history shows people's
  names, not emails (every role reads it).
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
  `pipeline.resolve()`. Both are idempotent on replay. One parse per show at
  a time: `process_show` takes a per-show advisory try-lock and raises
  `ParseBusy` (the route answers 409, the job re-queues itself). Shop-wide
  buyer lookups go through `lower(buyer_username)`, which
  `ix_sales_org_lower_buyer` covers; keep new ones on that shape.
- **Jobs.** `api/app/services/jobs.py` is the queue (`SELECT … FOR UPDATE
  SKIP LOCKED`); handlers register with `@handler("kind")`. `JOBS_INLINE=1`
  runs them synchronously (dev, e2e, CI); production runs `python -m
  app.worker`. `parse_show` is claimed before other kinds; `requeue_stale`
  fails a stale `running` job whose last allowed attempt never finished
  (instead of re-queueing a job that kills its worker forever). The worker
  survives a database outage (backs off) and never exits on one.
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
  Shipments, picks the show in Whatnot's show filter and presses "Send
  shipments" in the panel. It reads every page by column header and opens
  each row (Expand) for its lots: listing title, Whatnot order number, qty,
  price, shipping, sale type (`whatnot_lots`, unique per order number). The
  show comes from the filter label ("<title> Oct 22•1:44 AM EDT", shop zone,
  5 AM rollover); "All Shows" is refused. Row Order Dates are only "Oct 22"
  (no year/time) and are a fallback for older scrapers. With lots read, the
  order slips ARE the final pick list: one row per Whatnot lot, joined to a
  sale by `external_order_id`, else buyer + lot number (+ price); the product
  is the sale's SKU, else "No Pick" plus what the winner said in chat
  (`sale_chat`) — the winner picks the product in chat. Read-only: never
  changes a sale, order, payment or stock; only the username is kept.
- **Whatnot Seller API** (`services/whatnot/`, `docs/WHATNOT_API.md`). Whatnot decides
  what was sold, whether it was paid and where a parcel is; the app decides which
  product, the gifts, picking and packing. `client.py` is the only code that calls
  Whatnot (shared 8 req/s budget in `throttle.py`; a write is retried only when safe).
  The token and the webhook signing secret come from env (`WHATNOT_API_TOKEN`,
  `WHATNOT_WEBHOOK_SECRET`) and are never stored; there is no sandbox, so no test may
  reach the real API (`fake.py`, the `fake_whatnot` fixture; `WHATNOT_API=fake` for
  dev and e2e). A webhook is a hint: handlers read the resource again and never act
  on the body (`webhooks.py`, stored once by `webhook-id`). Orders run through the
  payment-file engine (`whatnot/orders.py` → `reconcile.py`, actor `whatnot`):
  `orders_mode` "shadow" only compares (Payments tab, a person applies), "auto"
  applies as they arrive; an order with no sale becomes one
  (`pipeline.create_sale_from_order`), and a later win line claims it
  (`claim_order_sale`) instead of making a second. **One package and one label per
  buyer per show** (`whatnot/shipping.py`): labels only after the show ended, never
  for a buyer in two open packages; label PDFs and links are fetched when printed
  and never stored (they show the address). Only the buyer's id and username are kept.
  A show's results and the financial ledger (`whatnot/results.py`, read-only, shown via
  `dashboard.whatnot_show` / `whatnot_ledger` to roles with `reports.view`): ledger
  lines are stored once by Whatnot's transaction id; the "net" is the sum of Whatnot's
  own signed lines in the shop's currency, and the app never derives one from the fee
  columns.
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
