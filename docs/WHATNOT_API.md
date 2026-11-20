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

**How the show is sold** decides what the laptop is for (Settings → How you sell; a show
can differ on its own page):

- *Winners pick in chat.* Whatnot's order says who won a "choose any" lot; the winner
  types a shelf spot in chat. The laptop records the chat. Ten minutes after the show ends
  the app reads each winner's lines against the show's shelves and leaves what it found on
  the win in Review ("Typed B4 in chat: Red bundle"). With "assign winners' picks" switched
  on in Settings, a pick is assigned with no person only when the order is paid, the
  laptop's clock was in step, the winner typed one clear spot after winning, and nothing
  about that buyer is in doubt.
- *One product per listing.* The order names the listing, and a linked listing names the
  product. With orders applied as they arrive the show needs no laptop: a show nobody
  records gets a place of its own ten minutes after its first order.

**After the show** (it ends → the app reads every order and every parcel of the show):

1. **Payments tab → Whatnot's orders.** One row per order item with what Whatnot says
   (paid / payment pending / payment failed / cancelled) and what that does here:
   - agrees with a sale → the sale is confirmed and tied to Whatnot's order number;
   - payment failed or cancelled → the sale closes and its stock returns, exactly as with a
     payment file (unless picking had begun: then a person decides);
   - a failed order followed by a paid one for the same win is a card that was retried
     (Whatnot keeps each attempt as an order: 10 of 20 lots on the 2026-11-13 show): the
     paid order is the win's payment and the failed one is left out. Their prices may
     differ by a discount one of them carries ($25.00 declined, $23.70 paid after $1.30
     off); a payment at exactly the declined price is still taken first;
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
   stream's decline closes the sale as before. Either way a decline from the stream lands
   only on the same win (`pipeline._declined_win`): the buyer's sale of the same lot by
   title at the price the player showed, or a sale an order made at the price after its
   discount and won within half an hour of the decline; never a sale whose own Whatnot
   order Whatnot already says is paid (a card declined and paid a moment later). A decline
   that matches nothing goes to a person, and so does one that matches such a paid win AND
   another win of the lot that is not.

   **An auction has one win.** An auction is a numbered lot of the show ("$1 BEAUTY PULLS
   #520"). Whatnot's paid order for a lot says who has it:
   - the payment is its buyer's sale for that lot, even when the laptop read another price
     (the difference is put to a person);
   - once the show has ended and all its orders are read, any other sale standing for the
     lot with no order of its own is cancelled and its stock returns: another buyer's (the
     lot was won again after their card was declined) or a second copy of the same win.
     If that buyer's own paid order turns up later, the win is recorded again;
   - a payment for another listing is never read as a declined card's retry;
   - what was open about the first buyer's declined payment is answered.
   On the stream, Whatnot's "won the auction" banner never cancels anything: the same
   buyer on the same lot again is one sale, and a second buyer on it is recorded with a
   "One lot, two winners" item on each sale Whatnot has not confirmed. Neither order is
   picked until a person or Whatnot's order settles it. A title with no lot number, the
   same number more than half an hour apart, and a "buy it now" order are left alone.

   **A declined payment is always put to a person in Review**, and the item clears itself
   when the payment goes through:
   - "Card declined: waiting for Whatnot": the stream showed the card declined and the sale
     still stands. Whatnot's paid order clears it; a failed one closes the sale. A person
     who is sure can close the sale at once. The buyer's order cannot be picked meanwhile.
   - "Payment declined": Whatnot's order (or the stream, in Compare only) closed the sale.
     "They paid after all" restores it by hand, but not while Whatnot's order for it says
     the payment failed: Whatnot decides, and when its order says paid, applying the orders
     restores the sale (with orders applied as they arrive, by itself).
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
   - once that read is done, a buyer with a paid item that is in no parcel on Whatnot that
     ships (none at all, their only parcel cancelled, or one item left out of their parcel or
     only in a parcel Whatnot cancelled, beside an open one) is listed in a group of its own,
     "No parcel on Whatnot". With no parcel at all: "Whatnot has no parcel for this buyer yet"
     (or "for N of this buyer's items"). "Create it in Seller Hub, then press Read again from
     Whatnot." (Javier's call, 2026-11-19). When Whatnot cancelled the item's only parcel,
     which it also does when a parcel's tracking got stuck (that one may have left): "Whatnot
     cancelled the parcel for … and made no other. Check that parcel in Seller Hub before
     packing: it may have left already. If it did not, create a new parcel there, then press
     Read again from Whatnot." The app never makes a parcel, and that buyer gets no label
     until the parcel is read (a label would close the bundle without the item), and is never
     marked shipped by the carrier's news of their other parcel. Before that read, or for an
     order whose payment is still being taken, an item in no parcel at all stays "not in a
     package on Whatnot yet" (the parcel may just not be read), and one only in a cancelled
     parcel leaves the buyer as they were ("the show is live", "waiting for the show's final
     list"), never ready. Two open parcels is still "combine them" first;
   - Whatnot may keep listing an item under the parcel it cancelled beside the one that took
     it: the item belongs to the parcel that ships, whichever is read last, and a parcel
     that does not ship never takes an item from one that does, as long as that parcel
     still lists it (asked of Whatnot at that moment, since a parcel's time may not change
     when an item leaves it). "Read again from Whatnot" reads a buyer's other parcel again when
     that buyer has an item stored on a cancelled one, so a row written before this rule, or
     an order read again that names the cancelled parcel, mends itself (a parcel with its
     label made, or with the carrier, only takes such an item back: nothing else about its
     stored list changes);
   - a parcel that also holds a win from another show that is live right now waits for
     that show to end; pickups never get a label;
   - a label is asked for once: two people pressing together, or a job run twice, still
     send one request. A request Whatnot did not clearly answer is not repeated for an hour;
   - "Print label and slip" opens Whatnot's own combined PDF. The app fetches it from
     Whatnot at that moment and passes it through: it is never stored, and neither is its
     link (it shows the buyer's address);
   - "Print N labels" opens the show's labels that have not been printed from the app as one
     PDF, in the order of the order slips, so labels and slips come off the printer in the
     same order. Up to 20 a press; the page says how many are still to print. Each label
     is fetched at that moment and the pages are joined in memory: nothing of them is stored,
     only the time each was printed, so a second press skips it. A label Whatnot does not
     hand over is left out, stays to print, and the others still print. One label can be
     printed again from its row at any time. Whatnot's packing slips can be left out.
     Opening the PDF is what marks a label printed (the app cannot see the printer); a label
     that did not come out (a jam, the tab closed first) is put back with the ones to print
     by "Not printed" on its row, one click, for whoever may print (Javier's call,
     2026-11-19). The next print fetches it from Whatnot again and marks it again; the
     activity log says who did it;
   - when the carrier has every parcel of a buyer, the order can be marked shipped in one
     press (or is, in "Apply as they arrive") with its tracking number, without clicking
     through picking and packing;
   - Orders → Shipped says where each parcel is, as Whatnot last said: waiting for the
     carrier, not with the carrier yet (three days after the order was marked shipped), on
     its way, delivered, or delivery failed, with the carrier and tracking number. The list
     can be narrowed to each of these. Parcels are read every 30 minutes while any is on
     its way, and at once when Whatnot sends news; nothing is asked of Whatnot when the
     page opens.

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
- **The dashboard** (same people): when the shop is connected, its money card, the
  Shows table's sales and Top buyers are Whatnot's for the last 7, 30 or 90 days: item
  subtotals of paid orders (no shipping or tax, giveaways counted but never added in),
  with the shipping buyers paid on those same orders said beside them, and the two
  together ("Sales with shipping": what Whatnot's own screens read; no tax, and no
  giveaway's shipping, in it), never added into Sales;
  split by new and returning customers (returning: a paid order from the same Whatnot
  account before the period, as far back as the orders the app has read go), and payment
  pending; failed payments are left out (a failed order is not a sale); the net
  payout and Whatnot's fees from the ledger as above, dated by the ledger's own lines;
  top buyers by what they paid, one row per Whatnot account under the name they use now.
  The packing role, the show pages, the live band and the Excel workbook keep the app's
  own counts. In those, a win's money is what Whatnot charged for it (the owner's call,
  2026-11-19; `dashboard.charged`): its line in the Whatnot order it is tied to, after any
  discount, whichever arrived first, the win line or the order ($25.00 on the player,
  $1.25 off: $23.75), else the win's own price. That is the tiles (paid, payment awaited,
  to pick, sold), the Revenue and Customers sheets, the Buyers page and the top buyers. The
  sale's own price is never rewritten (a decline and a second laptop's copy of the win are
  looked up by the price the player showed), so the Sales and Products sheets, the order's
  total on Orders and the products' money still read it.

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

**Offers and giveaways.**

- **Offers.** A win holds one product unless the host or a moderator listed in Settings →
  Trusted moderators types an offer that names the lot ("#45 bogo", "#45 is two for one",
  "#45 3 for 1"); "#45 is not bogo" takes it back, and a person's count on one win
  overrules the chat. The winner then types that many spots. A viewer's line never sets an
  offer. Fewer picked than allowed waits in Review as "picked 1 of 2".
- **Giveaways.** Whatnot lists a giveaway as an order of its own kind. It is never a sale
  here (not in the counts, not in revenue). Orders → Giveaways lists them for the shows the
  app follows; a person names the product each one was. Stock leaves then, comes back when
  the product is changed or taken off, and the product prints on the buyer's slip. A
  giveaway Whatnot cancelled does not ship; a product still on it is taken off by hand.

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
  listing marked "not one of ours" never names a product, not even by its title. A listing
  whose title says the winner chooses ("CHOOSE ANY…", `rules.is_choice_title`) names no
  product even when it is linked to one, by a person or by its SKU, in a show sold either
  way (Javier's call, 2026-11-19): each of its wins waits in Review for the winner's pick.
  Only a winner's-pick show reads that pick from chat by itself; in a show sold one
  product per listing a person names it.
- **Linking while naming a win.** In a show sold as one product per listing, an unnamed
  win reads "listing not linked". Naming it offers "Always use this product for this
  listing": the listing is linked, the show's other waiting wins of it are named at once,
  and later orders name themselves. Not offered in a winner's-pick show (there the listing
  is a "choose any" lot), for a win whose title says the winner chooses, or for a listing
  marked "not one of ours".
- **A paid win on a listing "not one of ours", its question ignored, is settled**
  (`orders.settled_unnamed_sales`). When Whatnot says the win is paid, its listing is marked
  "not one of ours" (and its title is not a "choose any" lot, which must still be named),
  and a person pressed "Ignore" on its "which product is that?" question with nothing else
  about it open in Review, it no longer holds the buyer's order back: the order is Ready to
  pick, also when that win is its only item, and goes through picking, packing and shipping
  by hand or when the carrier has the parcel. The win itself stays without a product: it
  holds no stock, is never marked paid or shipped here and is never on the pick list. Its
  money counts in "Paid revenue" (the tiles, the Revenue and Customers sheets, the Buyers
  page, the top buyers) at what Whatnot charged for it, its order line after any discount (the owner's
  call, 2026-11-19; `dashboard._settled_charged`); it is never a win "to pick". Its
  buyer's order slip is ready to print, its row as it always was (its title, or "No
  Pick"). The Orders page says "Paid on Whatnot · not one of our products". Linking that
  listing to a product asks the question again at once and the order waits for it (the
  next orders read names it by the listing, and the stock moves then); an order already
  being picked or packed keeps its step and waits there. Once the order has shipped with
  the win in it, linking the listing changes nothing: it is not asked again, never named
  from the listing, and stays settled. Cancelled or declined on Whatnot while its order is
  being picked, packed or shipped (and it was on the order when picking began), it opens
  the same "take it out of the package" note as a named item; nothing goes back on the
  shelf (paid after all on a retried card, the note is settled and the win waits for its
  product again). An order whose only item that was then stays where it is, exactly like an order
  whose only named item was cancelled after picking began (neither can be packed or sent
  back a step today). Deleting a laptop's recording keeps a person's "Ignore" of a win
  that stays (one tied to a Whatnot order).
- **A listing made during a show** is read when its first order is: the daily read pauses
  while a show is on air. A listing Whatnot no longer has is kept as it was sold, so a
  person can still say which product it was.
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
  tracking numbers, whether a label exists and when it was last printed from the app, each
  show's results, and the ledger's lines
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
| Older orders: one read, newest first, back to the dashboard's longest period with no cap (the normal catch-up stops at 3,000 orders of any kind and reads only the last three weeks of shows), then a daily top-up of the last days (orders placed between shows). Stores orders and items only: never marks a show read, makes a sale, applies an order or moves stock itself (what it stores is then re-read by the timer like any order, so an unsettled order of a show followed in "auto" mode is applied when it settles). Queued by the timer, never while a show is on air, in slices of about 90 s that each queue the next. The end of Whatnot's list only counts as the floor when two reads agree. Says on the dashboard which days every order is read for (`ordersCompleteSince` to `ordersCompleteUntil`) | `backfill.py` |
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
- **Known limit, and how a catch-up ends.** Orders are listed newest first with no filter.
  A catch-up stores at most 3,000 orders new to the app per pass (`sync.MAX_NEW_ORDERS`)
  and walks at most 400 pages (`MAX_LISTED_PAGES`). A show is marked read only when the
  read got down to its own start; the shows it did not reach stay unread and the next pass
  carries on past the orders already held. (Until 2026-11-19 a stopped catch-up marked
  every show read: shows beyond the newest 3,000 orders of the first connection hold none
  or few of their orders here, and the dashboard's 30-day sales fell short of Whatnot's.) The
  first pass after that fix reads every ended show of the last 30 days again, once per shop
  (`sync.begin_repair`, `_unread_shows`; waits while a show is on air); only the orders the
  app does not hold cost calls. A walk that hits its page limit with nothing new to store
  is not repeated for the shows it cannot reach for six hours (`sync.STALLED_RETRY`). A show
  older than 30 days is not read by the timer: "Read again from Whatnot" on the show's
  Payments tab reads one show down to its own start.

## How reads stay complete

- **Results.** Whatnot says GENERATED while it is still counting (94 of 217 orders was
  read 25 minutes after one show). A GENERATED answer is asked again every hour until six
  hours after the show ended, then every six hours, until three days after it ended
  (`results.results_due`); never while a show is on air, and figures already generated are
  only replaced by newer GENERATED ones, never by "still working", "will not be calculated"
  or an answer with nothing readable in it.
- **Late orders.** A card declined at the win and retried is a new order, placed after the
  show ended. The ended show's orders are listed again at 1, 6 and 24 hours after the end,
  then at two and three days (`sync.LATE_READS`), not while a show is on air. Each of these
  reads queues the show's apply, so a COMPLETED order Whatnot cancels days later closes its
  sale by the usual rules (restocked; a "closed after picking" note instead once picking
  began), even when another read (the backfill's daily top-up, which applies nothing) stored
  the cancel first. The open-order re-read only asks orders Whatnot had not settled. The
  three days are `sync.PARCELS_AFTER`, the days a show's parcels are read while it is packed,
  and as long as its results are asked again; a later cancel comes only by webhook. Listing
  costs a call per 50 orders (some 10 to 13 calls a read at this shop's 6,000 orders a
  month), where asking each completed order by id would cost a call each. An order first
  stored by a parcel read is queued for apply like any other (`sync.queue_apply`). The
  parcels of a show that ended are
  asked for once its orders are read in full after the end (`sync._parcels_owed`), not while
  another show is on air: nothing is marked asked meanwhile, so the first pass after that
  show asks for them.
- **A read that fails part-way** keeps the orders already read (each is stored whole: an
  order whose items could not be read is taken out again) and does not mark the shows
  read. A failed show-list or open-order read is due again after two minutes
  (`sync.RETRY_AFTER_FAILURE`), not a whole interval later, and the pass goes on to
  everything behind it (`sync._attempt`). The orders an open-order pass re-read before a
  failure are kept and their shows applied. A repair of orders held with no items
  (`sync.repair_items`) takes them in turn, skips one Whatnot refuses and never stops the
  read it rides on.
- **Orders that cannot be read.** An order with no buyer does not stop the show: it is left
  out, and Review says so in words (`orders_left_out`). If more than one in five of the
  show's orders have no buyer (`orders.NAMELESS_ONE_IN`) the show is "blind": nothing is
  applied and no sale is called out as unpaid. While a paid order has no buyer, no sale of
  the show is called out as unpaid, none gives way to another win of its lot, and a failed
  order is not undone as a decline if that order may be its retry (Review says so): the
  same win, placed AFTER the failed orders, and one such payment holds back the attempts of
  ONE buyer's win (a card declined twice, then paid), the buyer whose attempt is the closest
  before it (`orders._hold_for_nameless`); the other buyers' are declines. A payment with no
  order time holds them only when they are all one buyer's. An order with an item naming no
  listing or in a state the app does not know, and an order the app holds no items for
  (Whatnot answered it before its items; `sync.repair_items` asks again, except for a
  cancelled or failed one), is counted too (`unreadable` items, `noItems` orders) and the
  same `orders_left_out` item names the first three by Seller Hub's order number and buyer
  (`#… from …`) and counts the rest; the Payments tab says the same. The names keep the
  orders' own order, so applying the same orders again rewrites the one item with the same
  words and a notice a person dismissed is not opened again for them.
- **Unverified.** Whether Whatnot's results `orders_count` includes giveaway orders, and
  whether real orders ever come back with no buyer, are not known; the repair above does not
  rely on the first.
- **The open-order re-read** compares each order's status, not a count, and skips an order
  Whatnot refuses (five refusals in a row end the pass, and so does a pass in which every
  order was refused: that one is a failure, not a success).
- **A show Whatnot never ends** (`services/whatnot/showend.py`, Javier's call 2026-11-19).
  Whatnot can call a show ended and give no end time (the probe checks for exactly that),
  or leave it "live" or "stopped" long after it ended. Before, such a show was never read in
  full by the timer, never complete, never owed its parcels, picks or results, and no label
  was ever offered. One function now decides "over" for every reader (the timer's reads,
  "every order read", the Shipping page and labels, the picks, results, alerts and "is a
  show on air"):
  - Whatnot's own end time always wins and is never written over. The app stores no end of
    its own: it works it out each time, so a real end time that arrives later simply takes
    over, and what was already read after it is not read again.
  - Otherwise the show is over **12 hours after it began** (`ON_AIR_AT_MOST`, the same 12
    hours that already stopped a stuck show holding back the timer): from its start, else
    when the app saw it go live after knowing it as scheduled (`whatnot_shows.live_seen_at`;
    never a scheduled time older than that, so a show started a day late is not over the
    moment it goes live), else the time it was scheduled for, else (only while Whatnot calls
    it on air) when the app first saw it. A show first seen already ended with no time at all
    has no end until a laptop gives one: when it was first seen is not when it began.
  - **A show laptop can make it sooner.** When the laptop's tab is moved on to another
    channel, the extension tells the server once, in a batch with no lines, after the show's
    lines (`show.movedOnAt`; the server keeps the time IT heard it, `shows.moved_on_at`,
    never the laptop's clock). That is the end when it is before the 12-hour one, not before
    the show began, and no chat of the show's recordings reached the server after it. While
    Whatnot still calls the show live or paused, it also needs **proof nothing sold after
    it**: every order of the show read at least 30 minutes after the move, and none placed
    after it (2 minutes allowed for the last win's order; an order with no time counts from
    when the app first stored it, `whatnot_orders.first_stored_at`). A quiet half hour nobody
    read is no proof: the show stays on air until that read, which the timer makes even while
    webhooks arrive. **A show that keeps selling stays on air whatever a laptop saw**. For a
    show Whatnot calls ended there is no order check, because a card declined at the win and
    retried is a new order placed after the end, and it must not take back a show already
    complete.
  - **The host may only have paused.** A show over by this rule that Whatnot still calls live
    or paused with no end time keeps its orders read on every quiet timer pass until its 12
    hours have passed and one read after them (tried for a day). An order placed after the
    laptop's move puts it back on air at the next pass: labels, picks and "every order read"
    wait again, and a win called out as "not among Whatnot's orders" closes itself when its
    order comes (paid, or still settling). Not undone: a label made or a pick read while it
    looked over; that Review item for a win whose order never comes; a second win of a lot
    that gave way to another buyer's paid order (cancelled, stock back; its own order arriving
    later records the win again); and the sale's "needs a decision" flag, which stays after
    the item closes.
  - A show Whatnot calls ended is "ended" on the Shipping page at once (it says "not read in
    full" until the reads are done); what needs every order read after the end waits for
    that end to pass. A show left live past the 12 hours is shown as Whatnot's "Live" with
    "(treated as over: …)" on its Payments tab, and is history for "Apply as they arrive" if
    that end was before the choice.
  - **Not verified on the live shop**: how often Whatnot leaves out the end time or leaves a
    show live, and whether it really moves a viewer on to another channel at every show's
    end. The channel reader's selectors are not verified either (docs/PLAN.md §0), so the
    laptop's word may never come; the 12-hour rule is the backstop.

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
11. Shows Whatnot never ends: how often an ended show comes with no `ended_at`, or a show
    stays `live`/`stopped` after it ended, and whether Whatnot moves every viewer on to
    another channel when a show ends. The app's rule (12 hours, or sooner on a laptop's word)
    is in "How reads stay complete".
11. Listings: whether the shop's listings carry a SKU at all, how a "choose any" lot
    appears in the catalog, and whether an order item names its variant. Also unknown,
    and needed before the app could ever set Whatnot's stock: whether Whatnot counts a
    listing down at each sale, and what it does to the count on a cancel or a decline.
12. Offers: the words the shop's moderators really type. The app reads "bogo", "buy one
    get one", "two for one" and "N for 1" beside a lot number; that list is a guess.
13. Winners' picks on a real show: how often a pick is one clear spot typed after the win,
    and how far a laptop's clock drifts. Run shows with "assign winners' picks" off and
    read Review before switching it on.
14. Giveaways: that the order number Seller Hub's Shipments page shows for a giveaway is
    the API's order id (a scraped giveaway row shows its product only if it is).
15. A show with no laptop: never run on the live shop. In particular how soon after the
    win Whatnot lists the order, and whether an order always names its listing's variant.
