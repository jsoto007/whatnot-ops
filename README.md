# Sales Ops

Live-show operations for a Whatnot seller: a Chrome extension captures show
chat, a Flask API turns it into sales, inventory movements and orders, and a
React dashboard gives the fulfillment team everything at a glance.

See `docs/PLAN.md` for the phased build plan and `CLAUDE.md` for working
rules. Done so far: Phase 0 (scaffold), Phase 1 (self-healing extension
capture), Phase 2 (extension → API ingest), Phase 3 (accounts, roles,
invites, device keys from the UI), Phase 4 (products and the inventory
ledger), Phase 5 (rule-based parser, sales, review queue, job worker), Phase 6 (AI
supervisor), Phase 7 (orders, payment reconciliation, fulfilment), Phases 8–9 (dashboard,
analytics, Excel reports), Phase 10 (deploy config, runbook). Nothing is
deployed yet: `docs/RUNBOOK.md` has the first-deploy steps.

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
make api                        # Flask on http://localhost:5050
make web                        # Vite on http://localhost:5174 (proxies /api)
make seed-demo                  # local demo shop with three shows (sign-in details in api/app/cli/seed_demo.py)
make check                      # lint + typecheck + unit tests, both sides
make e2e                        # production build served by Flask + Playwright
make ext-e2e                    # extension: build, then Playwright against the fixture chat + the real API
make smoke BASE_URL=https://…   # health, SPA, fallback and 404 checks against a deployed instance
```

## Accounts

The shop's admin is named by the `ADMIN_EMAIL` environment variable. Only
that email can create the shop on `/signup`, only once, and (in production)
only with the one-time setup code from the `SETUP_TOKEN` environment
variable; everyone else joins by invite. That account is always an owner: nobody can demote or
disable it in the app, and logging in restores it if the database says
otherwise (`api/app/services/admin.py`). Without `ADMIN_EMAIL`, production
never opens sign-up; development and tests stay open. Owners invite the team
from **Team** with a role:

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
movement, so parallel sales never lose each other's updates.

The **Inventory** page is built around the team's two physical jobs:

- **Receive stock** — a delivery with any number of lines; a line is an
  existing item or a brand-new one (name, SKU code, price, alert line)
  created as it is received. All-or-nothing, and the form's id makes a
  double-submit or retry a no-op (`receive:<id>:<line>` movements).
- **Count stock** — type what is on the shelf for any items; the sheet shows
  each difference before saving, blank rows are skipped, and only changed
  items write an `adjust` (`count:<id>:<sku>`).

Click an item for its panel: on shelf / held / available / sold, a quick
add or remove with a reason (Received, Found, Damaged, Giveaway…), its
low-stock alert line, and its full history in words ("Sold 2 to kat.c ·
Oct 18 show", "Paid by kat.c"). Filters (Buy more / Low / Out / In stock),
search across names, SKUs and chat names, and a recent-activity feed sit on
the page itself. The logic is `api/app/services/stock.py`; every change still
goes through `inventory.move()`.

**Import sheet** takes the team's CSV (an `Item` column plus `Current Stock`
or `Start of Show Inventory`): preview shows what would be created or set,
apply does it. Aliases on the Products page map the names used in chat
("red", "ultra pack") to SKUs for the parser.

## From chat to sales (the parser)

Every ingested batch queues a `parse_show` job (the `jobs` table; run
`make worker`, or set `JOBS_INLINE=1` to run jobs in-process as `make api`,
`make e2e` and CI do). `api/app/services/rules.py` classifies each line
(sale, claim, gift, cancel, payment failed, no-pick, listing, chatter) with
ordered regexes; `pipeline.py` turns a sale into a `Sale`, matches the item
to a SKU through aliases, reserves stock through the ledger, and releases
it again on a cancel / payment-failed line for that buyer. Anything a person
must decide lands in **Review**: unknown item (pick the SKU, optionally
remember the name), which of several open sales to
cancel, a sale-looking line from someone who is not the host or the
platform ("Not sure": **Record as a sale** makes it a real sale through the
same path the rules use, item from the last listing, stock held, order
opened; or Ignore it). Set the shop's Whatnot handle under **Settings** so host lines
are trusted.

Overselling is allowed: a sale for more than is on the shelf is still
reserved, and the SKU's available count goes negative. **Inventory** and the
dashboard show "buy N more" until a restock covers it.

The rules are checked against `api/tests/fixtures/chat_corpus.jsonl`, which
is **synthetic** until real shows are captured; `tests/test_rules.py`
prints precision/recall per kind. "Parse now" on a show re-evaluates only
lines that led nowhere; existing sales are never rewritten by rules.

## Orders and fulfilment

Every sale attaches to its buyer's **order** for that show (one per buyer
per show). An order is `open` until every standing item is paid, then
`paid` (verified), then a person moves it `picking → packed → shipped`
(shipping marks the items fulfilled and takes a tracking number). Orders
with nothing left are `cancelled`.

Payment truth comes from Whatnot's order export: on a show's **Payments**
tab upload the CSV (columns like Buyer / Username, Product / Item, Status,
Order ID, Sold Price). Preview shows what each row will do: paid rows mark
the matching sale paid and **commit** the stock (it leaves the shelf),
cancelled / refunded rows release it, rows with no matching sale and sales
the file did not cover go to Review. Re-applying the same file changes
nothing.

**Orders → To fulfil** is the queue of verified orders; the **Pick list**
for a show totals the quantities per SKU across those orders; an order page
is the packing slip (print it) with the buyer's history in other shows.

## Dashboard and reports

The **Dashboard** (`/app`) shows the sheet's tiles across all shows (items
sold, buyers, red / bundles / ultra / mega, gifts, no-picks, cancelled or
failed, paid and pending revenue, to fulfil, needs review), a live pill
when a show has had chat in the last 10 minutes, a per-show table, items per
show, top buyers and top products with sell-through, and low stock. Each
show page has the same tiles and an **Analytics** tab. Numbers come from
one module (`api/app/services/dashboard.py`) that the reports use too, so
the tiles and the spreadsheets always agree.

**Reports** (owners, managers, viewers) downloads an `.xlsx` for one show or
for a date range with sheets: sales by show, customers, products sold,
inventory remaining, cancellations and failed payments, revenue by show,
fulfilment list, stock movements. Money is written as currency cells.

## AI supervisor

Off unless `AI_PROVIDER` is set: `claude` (official Anthropic SDK,
`ANTHROPIC_API_KEY`, `CLAUDE_MODEL` defaults to `claude-opus-5-5`, structured
JSON output) or `jev` (`JEV_API_URL`, `JEV_API_KEY`, `POST /v1/decide`).
`fake` is a keyword-driven stand-in for tests and e2e, refused in production.
Providers are swapped behind one interface (`api/app/services/ai/`), and
every call is logged in `ai_calls` (provider, purpose, latency, confidence,
error), shown under Settings.

What it does (`api/app/services/supervisor.py`):

- When the rules open a review item for a low-confidence line or an unknown
  item, a job asks the provider to classify the line or pick the SKU among
  the shop's products and aliases.
- After each parse, an audit of the show runs (2 minutes later, deduped):
  the last 400 lines plus the open sales, asking for cancellations and
  payment problems the rules missed, missed sales and duplicates.
  "Run AI audit" on a show runs it now.
- Every answer is a proposal with a confidence. Only reversible actions
  above the thresholds in `ai/policy.py` are applied on their own (cancel /
  payment failed on a reserved sale, assign a SKU at ≥ 95%); they go through
  the same functions a person uses, with actor `ai` in the audit log.
  Marking a sale paid is never automatic. Everything else waits in Review
  with an "Apply suggestion" button.

`flask ai-eval` replays the (synthetic) rules corpus through the configured
provider and prints the agreement.

## During a show (the panel)

On a show page Chat Scraper floats a small panel. Press **Start** and click
one chat message; from then on the one big button is **Stop & Save**, which
also downloads the chat as a spreadsheet. The dot in the header says how it is
going: **green**, breathing, means recording, chat in view and everything
uploaded; **amber** means it is sorting something out by itself (the chat
scrolled off screen, Sales Ops unreachable and retrying, lines set aside);
**red** needs a click (this laptop is not connected, was disconnected in
Settings → Laptops, or the extension was reloaded and the page needs a
refresh); **blue** waits for the click on a message; grey is not recording.

Three tabs: **Live** (status, wins, the button, and a "running low" row that
opens this show's low-stock list), **Find** (what a buyer said, live) and
**Flags** (lines matching the watch words, and the words themselves).
Everything else is behind **⋯**: Chat Search, a backup copy, Settings and
Clear. The minus button shrinks the panel to a pill with the dot and the
message count; click the pill to open it again, drag either to move it,
double-click the header to put it back in the corner. Wins ("tiffybid won ·
$19.00"), "2 left" and "Sold out" pop up beside the panel or the pill for five
seconds, newest at the bottom, three at most; click one to dismiss it.

## Connect a laptop

**Settings → Laptops** (owner or manager):

1. **Download** the extension. The zip is built from `extension/dist` on the
   server and already points at this portal. Unzip, open
   `chrome://extensions`, turn on Developer mode, **Load unpacked**.
2. **Connect a laptop** → name it → the portal shows an 8-character code
   (valid 10 minutes, single use). In the extension's Settings (toolbar icon
   → Settings) type the code and press **Connect**. The extension trades it at
   `POST /api/pair` for its own device key and the portal flips to
   "connected". Only the code's hash is stored; one conditional `UPDATE`
   decides who redeems it.

Advanced: **Create a device key instead** (or
`flask devices create --org "Shop name" --name "Front laptop"`) shows a
`wn_…` key once, to paste under "Use a device key instead" in the extension.
Every captured line is saved on the laptop first and uploaded in batches to
`POST /api/ingest`; uploads retry with backoff when the server is unreachable
and resume when the key is fixed.

### Update a laptop

Never remove the extension to update it: that drops its pairing and any lines
it has not uploaded. Update in place instead. Every device call sends
`X-Extension-Version`, so **Settings → Laptops** shows each laptop's version
with an **Update** badge when it is behind the build this portal serves. On a
laptop running 1.5.0 or later, the web app also shows a banner. Its **Update**
sheet walks through: download, copy the files over the folder Chrome loads the
extension from, then **Reload Chat Scraper**. The download's manifest names
this portal: `portal.js` tells the page the extension's id and version, and
`externally_connectable` lets the page ask the background for `hello` (running
version, version of the files on disk, recording?) and `reload`. The sheet
enables Reload once the new files are on disk and confirms the new version
came back. Developer mode must stay on, because Chrome disables a reloaded
unpacked extension without it. Laptops on 1.4.x and older need one manual
update (↻ in `chrome://extensions`) before the button works.
