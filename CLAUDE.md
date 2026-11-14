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
  survives a database outage (backs off) and never exits on one. An error marked
  `final` (Whatnot's `Invalid`: a 4xx request error) fails its job at once.
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
  **Where Whatnot's orders are applied as they arrive** (`whatnot/orders.decides(show)`:
  mode "auto", the recording matched to a followed show, a token that can read orders
  with their buyers), only Whatnot's order or a person declines a sale: a decline read
  from the stream (the player, a host's line, the AI's reading of chat) closes nothing,
  because half the cards declined at the win are paid a minute later. **A declined
  payment is always put to a person** (Javier's call, 2026-11-13), and the item clears
  itself when the payment goes through: the stream's decline flags the standing sale
  (`pipeline.flag_decline`, reason `decline_seen`), and the buyer's order cannot move
  into picking while that item is open (`orders.card_in_doubt`); a sale Whatnot's failed
  order closes
  gets `payment_declined`; a failed order with no sale here gets `order_declined`
  (`whatnot/orders._flag_declines`). A declined win that never had a product and is then
  paid on Whatnot goes back to waiting for its product (`pipeline.await_product`, the
  one allowed step back: no stock was ever held, and never once the buyer's order has
  shipped); with a product it is paid again through `_repay`.
- **One win per auction** (Javier's rule, 2026-11-13). An auction is a numbered lot of
  one show ("$1 BEAUTY PULLS #520": `pipeline.auction_of`; a title with no lot number
  names none and may be won many times), and only within half an hour: lot numbers start
  again in the next show a recording runs into. **The stream never cancels a sale for
  it** (an adversarial pass proved four ways a banner alone cancels a paying buyer). On
  Whatnot's own "won the auction" banner, never a typed line: the same buyer on the same
  lot again is the same win (no second sale); a different buyer is recorded too and each
  unconfirmed sale gets a `lot_won_twice` item (`pipeline.ask_which_win`), which keeps
  both orders out of picking and off the ready slips (`orders.in_doubt`). **Whatnot's paid
  auction order decides** (`whatnot/orders._one_win`): the payment is its buyer's sale
  for that lot whatever price the laptop read; once every order of the show has been
  read after it ended, any other sale standing for the lot with no order of its own
  gives way (`pipeline.give_way`: cancelled, stock back); the first buyer's open decline
  is answered; a buyer whose sale gave way and whose own paid order turns up later has
  the win recorded again; a payment for another listing id is never a declined card's
  retry. "Buy it now" orders are left alone.
- **A sale's products are rows** (`services/sale_items.py`, table `sale_items`). A sale
  can hold several products ("two for one") and any of them can be changed any number of
  times. Stock is held row by row: the sale's FIRST row keeps the sale's own keys in the
  ledger ("sale" / "sale_repaid", the sale's id), a row opened later is keyed
  "<sale id>:<row id>" (`sale_items.key`), so `pipeline.set_sale_status`, `_repay` and
  `units_out` loop over `sale_items.rows(sale)`. Only `sale_items` makes or closes rows
  (`change`, `add`, `remove`: order locked, then the sale; once the order is being picked
  nothing is put back by itself and Review gets a `swapped_after_picking` note that no
  decline or cancel sweeps away). `Sale.sku_id` is the first open row's product. Anything
  that counts units or money per product reads `sale_items.lines()` (SQL) or
  `sale_items.products(sale)` (Python), never `Sale.sku_id`/`Sale.quantity`: a win's price
  is split evenly in whole cents over its products, the odd cents to the first
  (`sale_items.split`), so products add up to the show's total. `inventory.move` locks
  the product row with `populate_existing`: keep it (a stale copy lost units).
- **One product at a time** (a show sold as "one product per listing"). The winner, the
  item and the payment all come from Whatnot's orders, so such a show needs no laptop when
  orders are applied as they arrive (`sale_mode.laptop_needed`; the show page and Settings
  → Laptops say so). A linked listing names its sale (`catalog.resolver`). An unnamed win
  reads "listing not linked"; naming it in Review offers "Always use this product for this
  listing" (`linkListing` on the resolve route → `catalog.link_sale_listing`), which links
  the listing and names the show's other waiting wins of it, each through
  `pipeline.resolve` as the person. Never in a winner's-pick show (there the listing is a
  "choose any" lot) and never for a listing marked "not one of ours". A listing made during
  a show is read when its first order is (`catalog.learn_listings`, from
  `sync._after_orders`, a few per pass inside a SAVEPOINT): the daily catalog read pauses
  while a show is on air. "Check now" in Settings also queues a read. A product is changed
  from the Orders row menu or the order's own page (`ChangeProduct`).
- **Offers** (`services/offers.py`). A win holds one product unless the host or a trusted
  moderator (`Organization.trusted_mods`, set in Settings) types an offer that names the
  lot ("#45 bogo", "#45 is two for one"; the words are only in `rules.offer`), or a person
  sets `Sale.products_allowed` on that one sale. The latest word on a lot stands, only for
  its show. A viewer's line never sets an offer (`tests/test_offers.py`). The pick worker
  takes up to that many spots from the winner (`picks._final`, `add_extras` through
  `sale_items.add`); fewer than allowed is left for a person ("picked 1 of 2").
- **Giveaways** (`services/giveaways.py`, `/app/orders/giveaways`). A giveaway in
  Whatnot's orders is never a sale: no price, not in any sales count or revenue, untouched
  by the pick worker. It waits in "Giveaways to assign" until a person names its product
  (`giveaway_items`, keyed by Whatnot's id of the order line; one open row per giveaway,
  under an advisory lock). Stock leaves when the product is named and comes back when it
  is changed or taken off, each row under its own ledger key ("giveaway", row id). Only
  Whatnot's orders make one: a chat "won the giveaway!" line never does. On the slips,
  Whatnot's giveaways replace the ones read from chat and print the chosen product.
- **The pick worker** (`services/picks.py`, job `pick_show`). In a winner's-pick show
  the winner names the product by typing a shelf spot ("B4"; `shelves.said` reads each
  win's final pick). Ten minutes after the show ends on Whatnot, the wins still waiting
  are settled: the product is named through `pipeline.resolve` (actor "pick", the spot
  kept on the decision) only when the shop switched it on (`Organization.auto_pick`, off
  by default) AND the line is the win's own buyer's, after the win and before the show
  ended, naming exactly one spot with a product in use, AND Whatnot holds the win's
  order as paid, AND every laptop that recorded the chat is KNOWN to be in step
  (`picks.clocks_sure`: "cannot be told" is not enough), AND nothing about the sale is in
  doubt. Everything else is left to a person with what was found on the review item
  (`proposal["pick"]`: the pick and why it was held, did not pick, a spot with no
  product, several spots, or "not read" when a clock was out). Never loosen a gate
  without a money-safety review: chat is typed by anyone watching.
- **Order slips** (`services/order_slips.py`, `/app/orders/slips/:showId`) are
  the per-show packing document staff used to build from Whatnot's shipments
  tab: a cover with totals, then one printed page per buyer. Auction Number
  comes from the lot title (`rules.lot_label`); "Gift N" is
  `Sale.gift_count`, set only by a person; giveaways are "Gift" rows and
  count as items.
- **Shelves** (`services/shelves.py`, `show_shelves`, the show's Shelves tab):
  optional per-show layout, "B4" = shelf B spot 4 → a product, laid out by
  people with `review.act`. Review only *suggests* the product on the spot a
  winner typed in their own latest line after the win (never remembered as a
  name); a person applies it. Its own table, so saving never moves
  `Show.updated_at`.
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
  applies as they arrive, but never a show that was over before "auto" was chosen
  (`sync.history`, `auto_since`: a first connection reads weeks of old shows whose orders
  keep changing); a failed order that a later paid order of the same win retried is left
  out (`orders._skip_retried`); an order with no sale becomes one
  (`pipeline.create_sale_from_order`), and a later win line claims it
  (`claim_order_sale`) instead of making a second. A recording belongs to the show its page address names
  (`/live/<id>`); the clock is only the fallback, never for a recording made on another
  stream's page when the show has its own (`sync.link_recordings`). A link is written with
  `sync.tie`, which leaves `Show.updated_at` alone: that column means "chat last arrived"
  (the Live pill, the show's length). **One package and one label per
  buyer per show** (`whatnot/shipping.py`): labels only after the show ended, never
  for a buyer in two open packages; label PDFs and links are fetched when printed
  and never stored (they show the address). Only the buyer's id and username are kept.
  "Print labels" for a show (`shipping.print_labels`, `POST /api/shipping/shows/<id>/labels/print`)
  joins the next labels not printed yet into one PDF in memory, in the order of the order
  slips, at most `LABELS_PER_PRINT` a time; only `label_printed_at` (a time) is kept, and it
  is set without touching the shipment's `updated_at` (read as when Whatnot's data changed).
  Orders → Shipped shows where each shipped order's parcel is (`shipping.parcels_of`: one
  word per order from the mirrored shipments, read-only, a fixed number of queries per list;
  no new order status).
  A show's results and the financial ledger (`whatnot/results.py`, read-only, shown via
  `dashboard.whatnot_show` / `whatnot_ledger` to roles with `reports.view`): ledger
  lines are stored once by Whatnot's transaction id; the "net" is the sum of Whatnot's
  own signed lines in the shop's currency, and the app never derives one from the fee
  columns.
- **What the winner said** (`services/sale_chat.py`, `services/shelves.py`). A sale made
  from Whatnot's order is placed in time by when Whatnot FIRST recorded an order for the win
  (`Sale.ordered_at`; for a card declined and then paid that is the declined attempt, set by
  `whatnot/orders._note_order_time`, and it only ever moves earlier). Chat is read from the
  win on (`picks_from`): a win read from the video player from its own line; a sale with no
  win line from the order's time less `ORDER_LAG` (3 s, a guess until the real gap is
  measured; kept short because a wide margin hands the buyer's previous win's pick to this
  one). One show on Whatnot is one chat however many recordings it was made in: every
  recording's lines are read, a line two laptops recorded counts once, a buyer's wins in all
  of them decide together which win a line goes to, and the shelf layout is the show's (saved
  to every recording; all use the oldest one's). **A laptop's clock is judged, never
  corrected** (`clocks`: in step, out, or cannot be told, from typed vs received times of
  minutes uploaded live). A "correction" from upload delays goes wrong by minutes when the
  Wi-Fi drops. A show with a laptop whose clock is out, or with two laptops not both known to
  be in step, gets no suggested picks (`unsafe`) and the show page says why
  (`clock_problems`). Anything that assigns a product from chat must go through `unsafe`. Do
  not read chat or a layout by `show_id` alone: use `sale_chat.around` / `shelves.read` /
  `suggest`.
- **Whatnot listings** (`services/whatnot/catalog.py`, `/app/inventory/whatnot`). The
  shop's Whatnot inventory is mirrored (`whatnot_variants`) and each listing linked to a
  SKU: by an exact code match, or by a person ("off" = none of ours, never re-linked).
  An order item for a linked listing carries its product (`reconcile.Row.sku_id`): a new
  sale gets it, and a win waiting for its product is named through `pipeline.resolve`
  (`name_from_listing`, actor `whatnot`), never by writing `Sale.sku` directly; a listing
  marked "off" never names a product, not even by its title (`Row.listing_off`). **The
  app never changes Whatnot's stock**: a push was built and removed after review
  (Whatnot counts its own sales down, and the app learns of a sale only when its order
  is applied, so setting a count puts sold units back). Do not add an inventory write
  without a design checked against the live shop. The daily catalog read waits while a
  show is on air (it shares the request budget the orders need). So do the hourly re-read
  of unsettled orders (every 6 h while webhooks arrive) and the listing reads for a
  "CHOOSE ANY…" lot with no SKU (the winner names the product; the daily read mirrors it).
  A labelled parcel's items are not asked for again (a label closes the bundle).
- **Selling mode** (`services/sale_mode.py`). A show sells one of two ways: "winners_pick"
  (a lot's title names no product; the winner says which one in chat, by its shelf spot,
  "A5") or "listing" (one product per listing; the listing's link names it). The shop has a
  usual way (`Organization.sale_mode`, the owner, Settings) and a show can differ
  (`ShowSaleMode`, managers, the show page); a choice is written to every recording of the
  same Whatnot show, and a recording that appears later follows the others. A listing linked
  to a product names its sale in either mode: the mode says what a win with no product waits
  for. Read a show's mode only through `sale_mode.of_show` / `of_shows`; the screens word it
  through `labels.ts`. The laptop learns it from `GET /api/ingest/stock` (`show`), with
  whether winners reach the app from Whatnot's own orders. A listing title that names one lot
  of one show ("… #520", "CHOOSE ANY…") is never learned as a product's name from a sale
  (`rules.names_a_lot`): lot numbers start again in every show.
- **Buyers** (`services/buyers.py`, `/app/buyers`). A profile row holds only who a buyer
  is (Whatnot's user id, every username they used) and what the team says (a note, a
  `vip` / `watch` flag, audited). What they bought is never stored: it comes from the
  sales by name through `dashboard.buyer_figures` (a "show" is a show date), with a
  person's names added up. Everything that changes who holds a name runs under the
  shop's buyers lock in a short transaction of its own: `link()` is a job queued after
  Whatnot's orders are read (never inside the order sync), `annotate()` is the team's
  save. A name moves to another Whatnot account only on a strictly newer, dated order (a
  tie or an undated order changes nothing, so reading everything again settles); a flag
  never follows a name to a different account, and `BuyerName.since_date` keeps what the
  earlier account won out of the new holder's figures (`dashboard.held_since`: use it in
  any new query that reads sales or orders by buyer name). The flag is shown beside the name on screen,
  never printed; the laptop (`POST /api/ingest/buyers`) gets the flag and counts, no note
  and no money.
- **Numbers.** Dashboard tiles and the Excel sheets both read
  `services/dashboard.py`; add a metric there, never inline in a route or
  the workbook builder. The gifts tile counts Whatnot's giveaway orders for a
  show linked to Whatnot, else the host/platform chat lines. Reports are built synchronously with openpyxl
  (`services/reports.py`); move them to a job if a shop outgrows that.
- **Query cost.** A page or job must not cost a statement per row: eager-load what the
  serializer reads (`selectinload`), and batch-load what a loop reads
  (`reconcile.load_sales`) instead of `session.get` per row. `tests/test_query_counts.py`
  and `tests/test_whatnot_efficiency.py` pin the counts (a few rows vs many); add a case
  for a new list page. Applying Whatnot's orders re-plans the whole show each run on
  purpose (retries, one win per auction and uncovered sales need every order); keep each
  run's statements constant instead. In the SPA, an action refreshes the keys it changes
  (`web/src/lib/invalidate.ts`), never a bare `invalidateQueries()`. A page opened is read
  again (laptops and Whatnot change data outside the tab: never give queries a staleTime
  that skips that); coming back to the window re-reads only data older than 15 s, and a
  4xx is never retried (`main.tsx`).
- **AI** only proposes. Providers implement `ai/base.py::AiProvider` and are
  chosen by `AI_PROVIDER` (`claude` via the official `anthropic` SDK with
  `output_config.format` structured output, `jev`, `fake` for tests). What may
  be applied without a person is only `ai/policy.py`; applied changes go
  through `supervisor.apply_proposal()` → the same pipeline/inventory
  functions as a human action, actor `ai`, audited. Never call a provider
  outside `supervisor.ask()` (it logs `ai_calls`). Tests use `FakeProvider`;
  no test may reach a real vendor.
- **Footer and legal pages** (`web/src/components/Footer.tsx`, `pages/Legal.tsx`:
  `/terms` and `/privacy`, open to everyone). The footer closes every page and is
  never printed. It says Sales Ops is *built to follow* Whatnot's policies and may
  change with them; never word that as a guarantee or as Whatnot's endorsement.
  Privacy lists what is really stored and who really receives it: change it, and
  `LEGAL_UPDATED` in `lib/legal.ts`, in the same PR that starts storing a new kind of
  data or sends data to a new service. The wording is a plain-language draft, not
  reviewed by a lawyer.
- **Licence.** Proprietary (`LICENSE.md`): the shop owns its data, SotoDev owns
  the software. Its promises are product rules: no client data in the repo
  (fixtures stay synthetic; `claude-reports/` is gitignored), no Client Data to
  an AI provider or tool not listed in §7.2/§7.4, and a new service that receives
  Client Data is a subprocessor (update §7.4, keep "Who else handles it" on the
  Privacy page saying the same, and tell Javier). After changing a runtime
  dependency of `web/` or `extension/`, run `make licenses` (`make lint` and CI
  fail while `THIRD_PARTY_LICENSES.txt` is out of date) and update
  `NOTICE.md`; keep the copyright banners in `web/vite.config.ts` and
  `extension/build.mjs`.
- **Licence acceptance** (`services/licence.py`, `licence_acceptances`): the shop's
  click-to-accept record (§2.2): version, who, when (UTC), client address, once per
  shop per version, written with a conditional INSERT and audited. Sign-up refuses
  without the box ticked; a shop with no record for the current version asks its
  owners (never staff, never the IT support account) with a banner until one accepts
  (`POST /api/auth/licence`, the version the page showed). Publishing a new
  `LICENSE.md` version means changing `LICENCE_VERSION` beside it (a test keeps them
  equal); every shop is then asked again. People read the terms at `/license`
  (`pages/LicenceTerms.tsx` draws the served `LICENSE.md` with `components/Markdown.tsx`,
  as text, never HTML): keep the file to the Markdown `lib/markdown.ts` knows, or the
  sign-up browser test fails. The Privacy page lists this record.
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
