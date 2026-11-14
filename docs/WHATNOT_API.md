# Whatnot Seller API in Sales Ops

How the app uses Whatnot's Seller API (REST v2, `https://api.whatnot.com/seller-api/rest/v2`),
how to set it up, and how the shop works with it. The plan this came from, and what is still to
build, is in `claude-reports/Whatnot-Seller-API/one-stop-shop-plan-2026-11-10.md`.

**Whatnot decides what was sold, whether it was paid and where a parcel is. The app decides
which product it was, the gifts, and the picking and packing.**

> **Read from the live API since 2026-11-12, not yet run through a live show.** It is built
> from Whatnot's published OpenAPI description (copies in
> `claude-reports/Whatnot-Seller-API/spec-2026-11-10/`) and tested against an in-memory fake
> that follows it. Whatnot has no sandbox. The probe and the first connection of the live
> shop are written up under "Seen on the live shop" below; a whole show with the
> connection on, labels and webhooks are still unverified.

## Setup

1. **Token** (account owner, Seller Hub → Seller API → Personal access tokens). Scopes:
   `read:orders`, `read:shows`, `read:shipments`, `read:customers`, `read:inventory`,
   `read:merchandising`, `read:reports`, `write:reports`. Add `write:shipments` later, by
   editing the token, if the shop wants to make labels from the app. Never tick
   `write:orders` (cancel + refund) or `write:inventory` for this app: nothing in it
   changes an order or Whatnot's stock.
2. **Server.** Set on the web service **and** the worker (Render → Environment; never in git):
   - `WHATNOT_API_TOKEN` — the `wn_pat_tk_…` token.
   - `WHATNOT_WEBHOOK_SECRET` — the `wn_wh_…` signing secret (web service only needs it; set
     it on both to keep them the same). Without it the webhook address answers 404 and the
     app reads Whatnot on a timer instead.
3. **Connect.** The owner opens Settings → Whatnot → **Connect to Whatnot**. The app asks
   Whatnot whose token it is and shows "Connected as @…". Nothing is typed there: the token
   never leaves the server.
4. **Webhooks** (after step 3, never before the app is deployed: a failing address is
   throttled, then unsubscribed). Seller Hub → Seller API → Webhooks → add the address
   Settings shows (`https://<app>/api/webhooks/whatnot`) and tick: Order created / updated,
   all five Show topics, all eight Shipment topics, and Product created / updated / deleted
   (18 of the 20). "Product added to show" and "removed from show" are not used yet.
5. **Probe** (once, from a shell that has the token):
   ```bash
   cd api && flask --app wsgi.py whatnot-probe
   ```
   About 30 slow GET requests; prints what shape the data has (which fields are filled, what
   an id looks like, which statuses occur) and no names, amounts, ids or links. It answers
   the open questions listed under "Not verified" below.

Local work and the browser tests use `WHATNOT_API=fake` (refused in production): an
in-memory Whatnot, seeded through `POST /api/whatnot/fake`. A test process never reads the
real token.

## How the shop works with it

**Before a show.** Shows → "On Whatnot" lists what is live and scheduled.

**During the show.** The laptop still records chat and the video player's wins (the winner
names the product in chat; Whatnot never knows it). Whatnot's orders arrive as they are
placed. Each recording is matched on its own to the Whatnot show that was live while it ran
(a person can change the match on the show's Payments tab).

**After the show** (it ends → the app reads every order and every parcel of the show):

1. **Payments tab → Whatnot's orders.** One row per order item with what Whatnot says
   (paid / payment pending / payment failed / cancelled) and what that does here:
   - agrees with a sale → the sale is confirmed and tied to Whatnot's order number;
   - payment failed or cancelled → the sale closes and its stock returns, exactly as with a
     payment file (unless picking had begun: then a person decides);
   - a failed order followed by a paid one for the same win is a card that was retried
     (Whatnot keeps each attempt as an order: 10 of 20 lots on the 2026-11-13 show): the
     paid order is the win's payment and the failed one is left out;
   - an order and a sale are the same win only when the lot matches by title (or both name
     the same known product), the price agrees when the sale knows what was paid, and the
     order was placed within half an hour of the win line. An order never takes "the
     buyer's only sale" just because it is the only one;
   - paid on Whatnot with no sale here → **the sale is recorded** (a win the laptop missed).
     A listing that names a product the shop knows is reserved and paid at once; "CHOOSE ANY
     PRODUCT #73" waits in Review for its product, like any win;
   - a sale here with no order on Whatnot → put in Review, but only once the show has ended
     and all its orders were read.

   - declined here before anyone named its product, then paid on Whatnot (a card retried a
     minute after the win) → **it waits for its product again**, as before the decline.
     With a product, it is marked paid again and the unit leaves the shelf once more.

   In **Compare only** (the default) nothing changes until someone presses Apply. In
   **Apply as they arrive** (owner, Settings → Whatnot) the same happens on its own, and
   from then on only Whatnot's order (or a person) declines a sale of a show the app
   follows: a declined card the laptop sees on the stream, or a host's line about it,
   closes nothing. Where the order cannot say (the recording is not matched to a show on
   Whatnot yet, Whatnot rejects the token, or the orders do not say who bought them), the
   stream's decline closes the sale as before.

   **A declined payment is always put to a person in Review**, and the item clears itself
   when the payment goes through:
   - "Card declined: waiting for Whatnot": the stream showed the card declined and the sale
     still stands. Whatnot's paid order clears it; a failed one closes the sale. A person
     who is sure can close the sale at once. The buyer's order cannot be picked meanwhile.
   - "Payment declined": Whatnot's order (or the stream, in Compare only) closed the sale.
     "They paid after all" restores it by hand; with orders applied as they arrive, the
     paid order restores it by itself.
   - "Payment declined: nothing recorded": Whatnot declined a payment for a win the app
     has no sale for. Nothing to undo; if they pay, the paid order records the sale.
2. **Order slips.** With Whatnot's parcels read, the slips are the final pick list (one row
   per Whatnot lot) with no "Send shipments" step on the laptop. The scraper still works and
   both feed the same tables.
3. **Shipping** (Orders → pick the show → Shipping, or the show page → Shipping).
   **One package and one label per buyer.** Whatnot keeps everything a buyer wins in one
   bundle until its label is made; making the label closes the bundle. So:
   - labels are made after the show ends, never during it;
   - "Make N labels" asks Whatnot for the label of every buyer whose wins are all in one
     open package (it says first what that closes; managers and owners only);
   - a buyer in **two open packages** is called out ("combine them in Seller Hub first") and
     is never sent for a label: the API cannot merge parcels, and two labels is the one
     thing that must not happen;
   - a parcel Whatnot is holding (a pending cancellation: **do not pack**) says so;
   - a later win after the label exists is "a later win needs its own label";
   - nobody is ready until the show's final orders and parcels were read after it ended
     ("Read again from Whatnot" reads both), so a last win is never left out of its bundle;
   - a parcel that also holds a win from another show that is live right now waits for
     that show to end; pickups never get a label;
   - a label is asked for once: two people pressing together, or a job run twice, still
     send one request. A request Whatnot did not clearly answer is not repeated for an hour;
   - "Print label and slip" opens Whatnot's own combined PDF. The app fetches it from
     Whatnot at that moment and passes it through: it is never stored, and neither is its
     link (it shows the buyer's address);
   - when the carrier has every parcel of a buyer, the order can be marked shipped in one
     press (or is, in "Apply as they arrive") with its tracking number, without clicking
     through picking and packing.

   Making labels from the app needs `write:shipments` on the token. Without it the page says
   so, and labels made in Seller Hub show up here all the same.

**The numbers** (for those who may read reports; the packing role does not see them):

- **A show's Analytics tab → "On Whatnot".** How Whatnot says the show did: sales, its
  estimate of earnings, buyers (first time and returning), viewers, reach. Whatnot works
  these out some time after the show; the app asks when the show ends and keeps asking for
  a few hours.
- **Money, from Whatnot's ledger** (the same tab, and Reports → "Fees and payouts, from
  Whatnot"). Whatnot's financial report lists what it credited and charged the shop, line
  by line: order earnings, shipping charges, refunds, carrier adjustments, tips. The app
  asks for it once a day (and on "Read from Whatnot now"), stores each line once by
  Whatnot's own transaction id, and shows each kind of line, their sum, and the fees
  Whatnot lists on the orders.
  **The net is the sum of Whatnot's own signed lines. The app does not work out a net of
  its own from the fee columns**: whether an order's ledger amount is before or after its
  fees is not documented, so adding or subtracting them could count fees twice. Only the
  shop's own currency is summed; lines in any other are counted and said.
  Refunds and carrier adjustments can arrive days later: a show's net can still move.

**Buyers** (the Buyers page; every role reads it, owners, managers and the packing table
write the flag and the note):

- One row per person: shows, items, what they paid, what fell through (cancelled wins and
  failed payments), giveaways won. Counted from the shop's own sales, so it works with or
  without Whatnot connected. A "show" is a show date.
- **A flag and a note from the team.** "VIP" (look after them) or "Watch list" (check
  before you ship). The flag shows beside the buyer's name on Orders, the order page and
  Shipping (never on a printed slip: a slip can end up in the parcel) and on the show
  laptop when they win. The note stays on the buyer's page.
- **A buyer who renames themselves keeps everything.** Whatnot's orders carry the buyer's
  account id, which does not change with the name. After orders are read, the app ties
  each name to its account: a new name joins the same person (history, flag and note
  follow), and a profile the team made for a bare name is tied to the account that uses
  it (the one that used the name most recently). A name that a *different* account
  starts using moves to that account only on a strictly newer, dated order; the earlier
  account's flag and note stay with the earlier account, and what was won under the name
  before that show date is not counted for the new holder. (Sales are filed by name, so
  those earlier wins are then on no buyer's page; the orders themselves are untouched.)
- **On the show laptop** (Chat Scraper 1.9.0): each win's notice says "New buyer" or
  "Back again · 12 items over 5 shows", with the team's flag first. Counted before
  tonight's show date. No note and no money reach the laptop.

**Listings and stock** (Inventory → Whatnot listings; everyone reads, owners and managers
link):

- **The shop's Whatnot listings next to its products.** The app reads the shop's Whatnot
  inventory once a day (and on "Read from Whatnot now"): each listing's title, Whatnot's
  SKU, price and how many Whatnot says are available. Reading changes nothing anywhere.
- **Linking.** A listing whose Whatnot SKU equals a product code here links itself. The
  rest are linked by a person, or marked "not one of ours" (which a later read never
  undoes). Inventory then shows "N on Whatnot" under what the shop can sell, and says when
  the two differ.
- **An order names its product.** An order for a linked listing is recorded with that
  product at once: no chat pick and no Review step. A win that was waiting for its product
  is named the same way when its order arrives (the decision a person would make in
  Review, recorded as made by Whatnot's record), at the price the order says was paid.
  "Choose any" lots are not linked to a product, so the winner still names it in chat; a
  listing marked "not one of ours" never names a product, not even by its title.
- **The app never changes Whatnot's stock.** It shows both counts and says when they
  differ; a person fixes the listing on Whatnot. A first version could set Whatnot's
  count from the app and was removed after its review: Whatnot counts its own sales down,
  and the app only learns of a sale when its order is read and applied (never, in
  "Compare only", or for a sale made between shows), so setting a number would put sold
  units back on sale. Doing it properly needs to be designed against how Whatnot's own
  counts behave on the live shop.

## What is kept, and what is not

- Kept: Whatnot's ids, statuses, listing titles, prices, the buyer's **id and usernames**
  (every name an account has used, so a rename is still the same person),
  tracking numbers, whether a label exists, each show's results, and the ledger's lines
  (amounts, fees, the buyer's username; not the buyer's state or country).
- Never kept: addresses, emails, phone numbers, label PDFs, label links, the webhook body
  (only the ids it names), the token, the signing secret.

## How it is built

| Piece | Where |
|---|---|
| The only code that calls Whatnot (pacing at 8 requests/s shared across processes, retries, typed errors, money as cents) | `api/app/services/whatnot/client.py`, `throttle.py` |
| Connection (which seller the shop is; problems people must act on) | `connection.py`, `routes/whatnot.py` |
| Webhooks: verify (Standard Webhooks HMAC), store once by `webhook-id`, hand to a job | `webhooks.py`, `routes/webhooks.py` |
| Mirror of shows and orders; matching recordings to shows; the timer | `sync.py` |
| Orders against sales: compare, apply, "auto" | `orders.py` (through `services/reconcile.py` and `pipeline.py`) |
| Parcels, the one-label rule, label requests and printing, carrier status | `shipping.py`, `routes/shipping.py` |
| A show's results and the financial ledger (request, download, import by transaction id, sums) | `results.py`; shown through `services/dashboard.py` |
| Buyer profiles: names tied to Whatnot's account id (its own job after orders are read, one writer per shop), the team's flag and note, figures from the sales | `services/buyers.py`, `routes/buyers.py`, figures in `services/dashboard.py`; the laptop asks `POST /api/ingest/buyers` |
| The shop's Whatnot listings (read-only), the link to products here, and an order naming its product | `catalog.py`; orders use it through `reconcile.Row.sku_id` and `pipeline.name_from_listing` |
| Read-only look at the live shop | `probe.py` (`flask whatnot-probe`) |
| In-memory Whatnot for tests | `fake.py` |

Rules that hold everywhere:
- **A webhook is a hint.** The handler reads the order, show or parcel again from the API
  and applies that. Deliveries can repeat, arrive out of order, or not arrive: the read when
  a show ends and the worker's timer catch what was missed.
- **Every change goes through the existing gates**: `pipeline.set_sale_status()` and the
  inventory ledger, with actor `whatnot` (the activity log and stock history read "Whatnot").
- **No test may reach the real API.** `client.make_client()` refuses in a test without a
  fake; tests use the `fake_whatnot` fixture.
- **Words, not codes, on screen** (`web/src/lib/labels.ts`); errors never name a setting, a
  scope or Whatnot's own text.

## Seen on the live shop (2026-11-12)

The probe, then the first connection in "Compare only" (reads only; one show compared).

- **Ids.** The API's order id is the 10-digit "Order #" of Seller Hub's Shipments page. A
  show's id is a UUID, and a viewer's page address (`/live/<id>`) carries it: a recording
  is tied to its show by that address, with the clock only as a fallback.
- **Lots.** Every auction lot is its own listing, "CHOOSE ANY PRODUCT YOU WANT #N", with
  no SKU; an order item names its product and variant ids. The inventory is thousands of
  such listings, so the daily catalog read stops at its cap and links nothing by code.
- **One show compared** (269 orders, 55 giveaways, 226 sales recorded here): every sale
  found its order; 25 sales held as paid were payment-failed on Whatnot (47 of the 269
  orders failed); 21 paid orders had no sale here. 11 of 162 buyers had more than one
  package.
- **Report requests** take times as `YYYY-MM-DDTHH:MM:SS.mmmZ` exactly (anything else is
  a 422).
- **A first connection reads weeks of ended shows.** Their orders keep changing for days,
  so a show that was over before the shop chose "Apply as they arrive" is never applied
  on its own (`sync.history`, `whatnot_connections.auto_since`); a person still can.
- **Known limit.** Orders are listed newest first with no filter, and a catch-up stops at
  3,000 orders: shows older than that are marked read although they were not. None of
  them has a recording, and none is applied on its own.

## Not verified

The probe answered 1–4; the rest need a real show with the connection on.

1. ~~Is the API's order id the "Order #" the Shipments scraper stores?~~ Yes.
2. ~~Does the live page's address carry the show's id?~~ Yes for a viewer's page (one
   show); the seller's own `/dashboard/live/` page is taken to carry it too, unverified.
3. ~~What do a "choose any" lot's title, SKU and product id look like in an order?~~ See above.
4. ~~Are `buyer` and `documents` filled with `read:customers`?~~ Yes, on every row read.
5. What is a won auction's status at the win, and when does it become `pending_fulfillment`?
   (The app treats `pending` as "sold, payment still being taken".)
6. Is the title in Whatnot's order exactly the title the video player shows? **This decides
   whether a win line and its order meet as one sale in "auto" mode.** Run 2–3 shows in
   "Compare only" first: pairs of "record the sale" and "sale with no order" for the same
   buyer mean they do not match yet.
7. Does making a label behave as documented (closes the bundle; result by webhook)? Does the
   shop's account need seller verification for it?
8. How long a label link lives; how fast webhooks arrive in a busy show.
9. The financial report's real file: its columns are the documented ones, but no report has
   been downloaded. In particular whether an ORDER_EARNINGS amount is before or after its
   fees (compare one order's line with Seller Hub the first time).
10. Buyers: that an account's id stays the same when the buyer changes their username, and
    whether an old order shows the name used then or the name used now. The app handles
    both, but no renamed buyer has been seen.
11. Listings: whether the shop's listings carry a SKU at all, how a "choose any" lot
    appears in the catalog, and whether an order item names its variant. Also unknown,
    and needed before the app could ever set Whatnot's stock: whether Whatnot counts a
    listing down at each sale, and what it does to the count on a cancel or a decline.
