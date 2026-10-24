# Sales Ops — phased build plan

Goal: turn the Chat Scraper extension + the team's spreadsheet into a role-based
web app that captures live-show chat, turns it into sales, reserves/commits
inventory, groups orders per buyer, flags doubtful cases for review, and gives
the fulfillment team one dashboard with everything they need.

Each phase below is built, tested and verified on its own, then merged to
`main` via a PR (rebase first, per `ship-pr`). No phase starts until the
previous one is green in CI and checked by hand. Nothing in this document is
built yet.

---

## 0. Architecture decisions

### Extension captures, server understands

The extension stays **dumb and reliable**: capture raw chat rows, assign
stable IDs, buffer locally, ship batches to the server. All parsing, AI,
inventory and order logic lives in the API.

Why:
- Parsing rules and AI prompts change weekly; a server deploy takes a minute,
  an extension change needs every laptop reloaded.
- The server has the database, product aliases, buyer history and an audit
  log. The extension has none of that.
- Raw messages are preserved verbatim and immutable, so any sale can be traced
  back to the exact lines that produced it, and parsing can be re-run on old
  shows when rules improve.
- A laptop closing mid-show must not lose data: the extension keeps an
  offline queue and the server ingest is idempotent, so retries never
  double-count.

The existing `.txt` download stays as a manual fallback.

### One repo, four parts

`~/Development/code/web-apps/whatnot-ops/` (name to confirm):

```
shared/        chat-event-batch.schema.json — the one contract between
               extension and API (validated with zod in TS, jsonschema in Python)
extension/     Chrome MV3 extension (moved from ~/Downloads/chat-scraper), TS + Vite build
web/           React 19 + Vite + TypeScript SPA (dashboard)
api/           Flask + SQLAlchemy + Alembic + Postgres (REST API, jobs, exports)
```

### Stack

- **API:** Flask app factory, SQLAlchemy 2.x, Alembic migrations, Postgres
  (local Postgres.app on :5432), pydantic for request validation, pytest
  against a `<db>_test` database. Money in integer cents. Timestamps
  tz-aware UTC via `datetime.now(timezone.utc)`, rendered in the shop's zone
  (the sheet's show-date rule uses America/Chicago, before 5 AM = previous
  night).
- **Web:** React 19, Vite, TypeScript, React Router, TanStack Query, plain
  CSS with a tokens file (no Tailwind), Vitest + Testing Library, Playwright
  for end-to-end. Charts per the `dataviz` skill.
- **Serving:** in production Flask serves the built `web/dist` (one Render
  web service, same-origin session cookies, no CORS). In dev, Vite proxies
  `/api` to Flask on :5000. `dist` is built on Render, not committed.
- **Background jobs:** a Postgres-backed job table consumed by a Render
  background worker (`SELECT … FOR UPDATE SKIP LOCKED`). No Redis to run or
  pay for; AI calls, re-parses, exports and the show audit all go through it.
- **Email:** Mailgun REST API via `requests`; unset key = log to console.
- **Deploy:** Render Blueprint (`render.yaml`): web service, background
  worker, Postgres (paid plan, not the 30-day free tier). `.python-version`
  and `.node-version` pinned.

### AI is a supervisor, never a writer

- `AiProvider` interface (Python) with two adapters: **Jev**
  (`POST /v1/decide`, input + output schema, returns fields + `confidence`)
  and **Claude** as a fallback / second opinion. Jev is early access and the
  public site is a community mirror, so the adapter boundary matters.
- A deterministic rule parser runs first on every message. AI only sees what
  rules could not resolve, plus a periodic "audit this show" pass.
- AI output is always a **proposal** (`ReviewItem`) with a confidence score.
  Above a per-type threshold it is auto-applied through the same state
  machine as a human action and logged; below it, it waits in a review queue.
  AI never touches inventory rows directly.

### Inventory is a ledger with atomic gates

No "current stock" column that gets overwritten. `InventoryMovement` rows
(reserve / commit / release / adjust / restock) keyed by the source event, so
re-processing a message is a no-op. Available = on-hand minus open reserves,
computed inside `SELECT … FOR UPDATE` on the SKU row. This is what prevents
duplicate deductions and lost updates. Overselling is allowed on purpose
(decided 2026-10-20): available may go negative, and the shortfall is shown
as how many to buy.

Sale lifecycle: `detected → reserved → paid → fulfilled`, with branches
`cancelled` and `payment_failed` that release the reserve. Every transition
writes an `AuditLog` row with who/what (user, rule, or AI + confidence).

### Payment truth

Chat tells us a sale happened; it does not prove payment. As far as I know
Whatnot has no public seller API (to verify), but the Seller Hub exports
orders as CSV. Phase 7 adds a CSV reconciliation import: matched orders move
`reserved → paid` and commit inventory. Until then, "paid" is a manual or
AI-proposed step. Confirm with the team which signal they trust today.

---

## 1. Open questions for the team (answer before Phase 5)

From the spreadsheet: `Auction #`, `Auction Type`, `Buyer Name`, `Item`,
`Gift`, `Running Inv`, `Start of Show Inventory`, `Items Sold This Show`,
`Qty Sold`, `Current Stock`; stat tiles `Items Sold`, `Buyers`, `Red`,
`Gifts`, `No-Picks`, `Bundles`, `Ultra/Mega (U · M)`.

- What are the Auction Types, and what do Red, No-Pick, Bundle, Ultra and
  Mega mean operationally? Do they change inventory differently?
- What does a Gift mean for inventory and for the packing list?
- What do the chat lines for a sale, a giveaway, a cancellation and a failed
  payment look like on Whatnot today? (Phase 1 captures real shows to answer
  this with data.)
- Do they export orders from Whatnot Seller Hub? If yes, a sample CSV.
- Who are the roles: owner, manager, packer, read-only?
- Price per item: in chat, in the sheet, or on the product?

---

## 2. Phases

Every phase ends with: lint + typecheck + tests green locally and in CI
(`ruff` + `pytest` for the API, `eslint` + `tsc` + `vitest` for web and
extension), a manual check listed under "Verify", a PR rebased onto `main`,
merged with your OK. Items under "Verify" that cannot run locally are
reported as "not verified — reason", never implied.

### Phase 0 — Repo and scaffolding

Build
- New private GitHub repo (needs your OK to create). Layout above.
- `api/`: app factory, config from env only, SQLAlchemy + Alembic wired,
  `GET /api/health`, pytest with a test DB fixture, `ruff`.
- `web/`: Vite React TS scaffold, router, tokens.css, Vitest, Playwright
  config, `/api` dev proxy.
- `extension/`: current files moved in unchanged, plus a Vite build so it can
  become TypeScript in Phase 1.
- `render.yaml`, `.python-version`, `.node-version`, `.env.example`,
  `Makefile` (`make dev`, `make test`, `make check`).
- GitHub Actions: API tests against a Postgres service, web and extension
  lint/typecheck/tests, Playwright against a production build.
- Repo `CLAUDE.md` with the DB safety rule (print the DB host before any
  seed/migration; local `.env` never points at prod), this plan moved to
  `docs/PLAN.md`.

Verify
- CI green on the empty-app PR. `make dev` serves the SPA through Flask.
  Extension still loads unpacked from `extension/` and records a show exactly
  as v1.2 does.

### Phase 1 — Extension capture reliability

Build
- Split capture into a pure TypeScript module (`extension/src/capture/*`):
  `findRow`, selector building, dedup, username/body split. Unit tests with
  Vitest + jsdom.
- Structured rows: `{ id, showId, username, body, rawText, capturedAt (ISO),
  seq }` where `id` is a hash of (show, username, body, 2-second bucket).
  This is the idempotency key for the server.
- Selector self-healing: when no new rows arrive for N seconds while the DOM
  is changing, re-run row detection from the last known container; surface
  "lost the chat, click a message" in the panel instead of silently stopping.
- Scope the MutationObserver to the chat container, not `document.body`.
- Move storage to IndexedDB (structured, large, queryable) with a migration
  from the current `chrome.storage.local` keys. Keep `cs_watchwords`.
- Capture 2–3 real shows with the hardened extension and save the exports as
  fixtures (`extension/fixtures/*.jsonl`). These become the parser corpus.
- A fixture HTML page that imitates the Whatnot chat DOM for Playwright
  extension tests (append rows, re-render rows, swap classes).

Verify
- Playwright: load extension, pick a row, append 500 rows incl. re-renders,
  assert exact count and no duplicates; swap the row class and assert
  self-healing.
- Live show run by a team member; row count matches a manual sample.

### Phase 2 — Shared schema and ingest API

Build
- `shared/chat-event-batch.schema.json`, versioned; zod type generated for
  TS, `jsonschema` validation in Flask; a test on each side runs the same
  fixture files through both validators.
- `POST /api/ingest` authenticated by a per-device API key (hashed in DB,
  seeded for now, managed in the UI in Phase 3). Upsert `ChatMessage` by
  `id`, auto-create `Show` from the stream's show key, return
  `{ accepted, duplicates }`.
- Extension: upload queue (IndexedDB), batches of ≤200 rows every 3 s or on
  200 pending, exponential backoff, offline buffering, status in the panel
  (`synced 1,204 · pending 0`), options page for server URL + key.

Verify
- pytest: idempotent re-send, partial failure, out-of-order batches, bad
  key. Playwright: fixture page → extension → local Flask → rows in DB.
- Live show end-to-end into a local DB.

### Phase 3 — Accounts, roles, invites

Build
- Users, Organizations (one per shop; multi-tenant from day one is cheap
  here), roles `owner · manager · fulfillment · viewer`, permission matrix in
  one module with tests, enforced by a decorator on every route.
- Email + password (argon2), secure session cookies, CSRF, password reset,
  invites by email via Mailgun, device API key management, audit log viewer.
- SPA shell with the navigation sections: Shows, Orders, Inventory,
  Products, Review, Reports, Team, Settings; route guards by role.

Verify
- pytest on the permission matrix and every protected route; Playwright:
  invite → accept → login → role-gated page denied/allowed. Mailgun send
  verified on a sandbox domain (or marked not verified if no credentials
  yet).

### Phase 4 — Products and inventory ledger

Build
- `Product`, `Sku`, `ProductAlias` (free-text names seen in chat → SKU),
  `InventoryMovement` ledger, `reserve/commit/release/adjust` service with
  `FOR UPDATE` gates and idempotency by `source_event_id`.
- CSV import of the team's current inventory sheet (start-of-show inventory
  per item), manual adjustments with reason, low-stock flags.
- Inventory UI: on-hand, reserved, available per SKU; movement history.

Verify
- Concurrency test (threads against the real test DB): 50 parallel reserves
  on a SKU with 10 available never exceed 10; replaying the same event twice
  moves nothing.
- Import the real spreadsheet and match the sheet's `Current Stock` column.

### Phase 5 — Deterministic parser

Build
- Rule-based classifier from the Phase 1 corpus: sale / gift / cancel /
  payment failed / no-pick / bundle / chatter, extracting auction #, buyer,
  item text, qty, price when present. Golden-file tests over the corpus with
  precision/recall numbers in the test output.
- Item text → SKU via `ProductAlias`; unmatched goes to the review queue.
- Pipeline: `ChatMessage → ParsedEvent → Sale + reserve`, run as a job on
  ingest and re-runnable per show ("re-parse this show" button). Live view of
  parsed events next to the raw lines.

Verify
- Precision ≥ 99% on sale lines in the corpus (number reported, not assumed).
  A re-parse of a show produces identical sales and zero extra movements.

### Phase 6 — AI supervisor

Build
- `AiProvider` interface; Jev adapter (`/v1/decide` with a strict output
  schema, confidence passthrough); Claude adapter as fallback. Retries,
  timeouts, cost logging per call. Keys from env only.
- Two jobs: (a) classify messages the rules could not; (b) per-show audit:
  find cancellations and payment problems in conversation, buyers whose
  purchases do not match sales, inventory discrepancies, duplicate sales.
- Thresholds per event type; auto-apply above, `ReviewItem` below. Review
  queue UI: approve / edit / reject, every action audited.
- Evaluation script: replay the corpus with human labels, print agreement.

Verify
- Eval numbers on the corpus. Playwright on the review queue. Live show with
  the audit job running, results reviewed by the team.

### Phase 7 — Orders, fulfillment, payment reconciliation

Build
- `Order` = one buyer × one show, aggregating their sales; status pipeline
  `open → paid → picking → packed → shipped`. Buyer profile with history
  across shows.
- Whatnot Seller Hub CSV import: match on buyer + item + show, move matched
  sales to `paid` and commit inventory, flag unmatched both ways.
- Fulfillment queue: verified orders only, picking list per order and per
  SKU, packing slip print view, bulk status updates, "questionable" badge.

Verify
- Playwright: full flow from ingest to a packed order. Reconciliation test
  with a synthetic CSV covering matched / unmatched / cancelled.

### Phase 8 — Dashboard and analytics

Build
- Show dashboard replicating the sheet's tiles (Items Sold, Buyers, Red,
  Gifts, No-Picks, Bundles, Ultra/Mega) plus revenue, open reviews, live
  feed while a show is recording (polling first; SSE if needed).
- Show picker, cross-show comparisons, top buyers, top SKUs, sell-through.

Verify
- Tile values match SQL on seeded fixtures; a seeded multi-show demo for
  the team.

### Phase 9 — Excel reports

Build
- `openpyxl` workbooks generated by a job and downloaded from Reports:
  sales by show, customer purchase history, products sold, inventory
  remaining, cancellations and failed payments, revenue, fulfillment lists.
  One sheet per report, consistent column order, cents rendered as currency.

Verify
- Files open in Excel and Numbers; totals equal the dashboard for the same
  show.

### Phase 10 — Deploy and operations

Build
- Render Blueprint (web service, background worker, Postgres paid plan),
  env checklist, Mailgun domain, backups, runbook (`docs/RUNBOOK.md`),
  extension packaged for the team with the production URL as the default.
- Later: shipping labels and tracking (EasyPost or Shippo) as Phase 11 once
  the fulfillment flow is stable.

Verify
- Smoke test against staging with a real show before anything points at
  production. Deploys only with your OK.

---

## 3. Decisions still needed

1. Project name `whatnot-ops` at `~/Development/code/web-apps/whatnot-ops`
   (or tell me another).
2. OK to create the private GitHub repo under `jsoto007`.
3. Jev access: do you already have an API key and docs from hello@jevai.net?
   If not, Phase 6 starts on the Claude adapter and Jev slots in later.
