# Whatnot Seller API in Sales Ops

How the app uses Whatnot's Seller API (REST v2, `https://api.whatnot.com/seller-api/rest/v2`),
how to set it up, and how the shop works with it. The plan this came from, and what is still to
build, is in `claude-reports/Whatnot-Seller-API/one-stop-shop-plan-2026-11-10.md`.

**Whatnot decides what was sold, whether it was paid and where a parcel is. The app decides
which product it was, the gifts, and the picking and packing.**

> **Not yet run against the live API.** Everything here is built from Whatnot's published
> OpenAPI description (copies in `claude-reports/Whatnot-Seller-API/spec-2026-11-10/`) and
> tested against an in-memory fake that follows it. Whatnot has no sandbox. The first live
> step is `flask whatnot-probe` (below); read its answers before switching anything to "auto".

## Setup

1. **Token** (account owner, Seller Hub → Seller API → Personal access tokens). Scopes:
   `read:orders`, `read:shows`, `read:shipments`, `read:customers`, `read:inventory`,
   `read:merchandising`, `read:reports`, `write:reports`. Add `write:shipments` later, by
   editing the token, if the shop wants to make labels from the app. Never tick
   `write:orders` (cancel + refund) for this app.
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
   all five Show topics, all nine Shipment topics. Product topics are not used yet.
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
   - an order and a sale are the same win only when the lot matches by title (or both name
     the same known product), the price agrees when the sale knows what was paid, and the
     order was placed within half an hour of the win line. An order never takes "the
     buyer's only sale" just because it is the only one;
   - paid on Whatnot with no sale here → **the sale is recorded** (a win the laptop missed).
     A listing that names a product the shop knows is reserved and paid at once; "CHOOSE ANY
     PRODUCT #73" waits in Review for its product, like any win;
   - a sale here with no order on Whatnot → put in Review, but only once the show has ended
     and all its orders were read.

   In **Compare only** (the default) nothing changes until someone presses Apply. In
   **Apply as they arrive** (owner, Settings → Whatnot) the same happens on its own.
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

## What is kept, and what is not

- Kept: Whatnot's ids, statuses, listing titles, prices, the buyer's **id and username**,
  tracking numbers, whether a label exists.
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

## Not verified (no live call has been made)

`flask whatnot-probe` answers 1–6; the rest need a real show.

1. Is the API's order id the "Order #" the Shipments scraper stores? (Decides whether old
   scraped lots join.)
2. Does the live page's address carry the show's id? (Today recordings are matched by time.)
3. What do a "choose any" lot's title, SKU and product id look like in an order?
4. Are `buyer` and `documents` filled with `read:customers`?
5. What is a won auction's status at the win, and when does it become `pending_fulfillment`?
   (The app treats `pending` as "sold, payment still being taken".)
6. Is the title in Whatnot's order exactly the title the video player shows? **This decides
   whether a win line and its order meet as one sale in "auto" mode.** Run 2–3 shows in
   "Compare only" first: pairs of "record the sale" and "sale with no order" for the same
   buyer mean they do not match yet.
7. Does making a label behave as documented (closes the bundle; result by webhook)? Does the
   shop's account need seller verification for it?
8. How long a label link lives; how fast webhooks arrive in a busy show.
