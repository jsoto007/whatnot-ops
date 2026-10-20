# Whatnot Ops

Live-show operations for a Whatnot seller: a Chrome extension captures show
chat, a Flask API turns it into sales, inventory movements and orders, and a
React dashboard gives the fulfillment team everything at a glance.

See `docs/PLAN.md` for the phased build plan and `CLAUDE.md` for working
rules. Done so far: Phase 0 (scaffold), Phase 1 (self-healing extension
capture), Phase 2 (extension → API ingest), Phase 3 (accounts, roles,
invites, device keys from the UI), Phase 4 (products and the inventory
ledger), Phase 5 (rule-based parser, sales, review queue, job worker).

## Layout

```
api/         Flask + SQLAlchemy + Alembic (REST API, jobs, exports)
web/         React 19 + Vite + TypeScript SPA
extension/   Chrome MV3 extension (Chat Scraper)
shared/      JSON Schema contract between extension and API
docs/        plan, runbook
```

## Run locally

```bash
cp .env.example .env            # local Postgres.app, see values inside
make db-create                  # creates whatnot_ops and whatnot_ops_test
make install                    # python venv + npm install
make api                        # Flask on http://localhost:5000
make web                        # Vite on http://localhost:5173 (proxies /api)
make check                      # lint + typecheck + unit tests, both sides
make e2e                        # production build served by Flask + Playwright
make ext-e2e                    # extension: build, then Playwright against the fixture chat + the real API
```

## Accounts

The first visit to `/signup` creates the shop and its owner (always allowed
for the very first account; afterwards only with `ALLOW_SIGNUPS=1` or
outside production). Owners invite the team from **Team** with a role:

| role        | can                                                              |
|-------------|------------------------------------------------------------------|
| owner       | everything, incl. roles, disabling users, settings               |
| manager     | invite fulfillment/viewer, device keys, inventory, review, reports |
| fulfillment | shows, orders, pick/pack                                          |
| viewer      | read-only shows, orders, inventory, reports                       |

The matrix lives in `api/app/services/permissions.py`. Invites and password
resets go out through Mailgun (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`,
optional `EMAIL_FROM`, `APP_URL` for the links); with Mailgun unset the
email is logged and, outside production, the link is shown in the UI.
Production bootstrap without sign-ups:

```bash
cd api && FLASK_APP=wsgi.py .venv/bin/flask users create-owner --org "Shop name" --email you@shop.com --name "You"
```

## Inventory

Products have one or more SKUs; each SKU keeps `on_hand` and `reserved`
(`available = on_hand - reserved`) as a cache of the append-only
`inventory_movements` ledger. Every change is a movement with a kind:
`restock`, `adjust` (manual, signed), `reserve` (a sale was detected),
`release` (cancelled / payment failed), `commit` (paid: leaves stock). A
movement with a source (`sale 42`) is unique per SKU and kind, so replaying
an event moves nothing, and the SKU row is locked for the duration of each
movement, so parallel sales can never reserve more than is available.

**Inventory → Import the inventory sheet** takes the team's CSV (an `Item`
column plus `Current Stock` or `Start of Show Inventory`): preview shows
what would be created or set, apply does it. Aliases on the Products page
map the names used in chat ("red", "ultra pack") to SKUs for the parser.

## From chat to sales (the parser)

Every ingested batch queues a `parse_show` job (the `jobs` table; run
`make worker`, or set `JOBS_INLINE=1` to run jobs in-process as `make api`,
`make e2e` and CI do). `api/app/services/rules.py` classifies each line
(sale, claim, gift, cancel, payment failed, no-pick, listing, chatter) with
ordered regexes; `pipeline.py` turns a sale into a `Sale`, matches the item
to a SKU through aliases, reserves stock through the ledger, and releases
it again on a cancel / payment-failed line for that buyer. Anything a person
must decide lands in **Review**: unknown item (pick the SKU, optionally
remember the name), not enough stock, which of several open sales to
cancel, a sale-looking line from someone who is not the host or the
platform. Set the shop's Whatnot handle under **Settings** so host lines
are trusted.

The rules are checked against `api/tests/fixtures/chat_corpus.jsonl`, which
is **synthetic** until real shows are captured; `tests/test_rules.py`
prints precision/recall per kind. "Parse now" on a show re-evaluates only
lines that led nowhere; existing sales are never rewritten by rules.

## Connect a laptop (extension device key)

**Settings → Laptops → Create key** in the web app (owner or manager), or:

```bash
cd api && FLASK_APP=wsgi.py .venv/bin/flask devices create --org "Shop name" --name "Front laptop"
```

Either shows the key once (`wn_…`). Paste it with the server URL into the
extension's Settings page (toolbar icon → Settings, or `chrome://extensions`
→ Chat Scraper → Details → Extension options). "Test connection" should say
which device and organization it is. Every captured line is saved on the
laptop first and uploaded in batches to `POST /api/ingest`; uploads retry
with backoff when the server is unreachable and resume when the key is
fixed.
