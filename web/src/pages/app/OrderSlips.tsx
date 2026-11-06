import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { api, apiGet, money, type BuyerSlip, type OrderSlips as Doc, type SlipRow } from '../../lib/api'
import { fmtLongDate, sheetDate } from '../../lib/format'

const count = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`
const slipTitle = (b: Pick<BuyerSlip, 'username' | 'items' | 'gifts'>) =>
  `Username: ${b.username} - ${count(b.items, 'Item')}${b.gifts ? ` + ${count(b.gifts, 'Gift')}` : ''}`
const GIFT_CHOICES = [0, 1, 2, 3, 4, 5]

/**
 * The show's Order Slips, laid out like the sheet staff used to build from Whatnot's shipments
 * tab: a cover with the totals and every buyer A-Z, then one printed page per buyer (most items
 * first) with a row to tick per item and the Ready To Ship / Gift(s) included / Packed by footer.
 */
export function OrderSlips() {
  const { showId = '' } = useParams()
  const { can } = useAuth()
  const q = useQuery({ queryKey: ['order-slips', showId], queryFn: () => apiGet<Doc>(`/api/orders/shows/${showId}/order-slips`) })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the order slips.</p>
  const doc = q.data
  const review = doc.slips.filter((b) => b.needsReview).length
  return (
    <section className="order-slips">
      <div className="no-print">
        <p>
          <Link to={`/app/orders?view=all&show=${showId}`}>← Orders</Link>
        </p>
        <div className="page-head">
          <div>
            <h2>Order slips · {fmtLongDate(doc.showDate)}</h2>
            <div className="sub">
              One slip per buyer, ready to print. Cancelled and declined items are left out. {can('orders.fulfill') ? 'Set "Gift" on a lot when free gifts go in with it.' : ''}
            </div>
          </div>
          <button className="btn primary" onClick={() => window.print()} disabled={!doc.slips.length}>
            Print order slips
          </button>
        </div>
        <WhatnotCheck doc={doc} />
        {review > 0 && (
          <p className="card small" data-testid="slips-review">
            {count(review, 'buyer')} still {review === 1 ? 'has' : 'have'} an item nobody has picked (shown as "No Pick").{' '}
            <Link to="/app/review">Open Review</Link> to name it before printing.
          </p>
        )}
      </div>
      {doc.slips.length === 0 ? (
        <div className="empty">
          <b>No order slips yet</b>
          Slips appear as soon as this show has a sale.
        </div>
      ) : (
        <>
          <Cover doc={doc} />
          {doc.slips.map((b) => (
            <Slip key={b.username} b={b} date={sheetDate(doc.showDate)} showId={showId} canEdit={can('orders.fulfill')} />
          ))}
        </>
      )}
    </section>
  )
}

/** Whatnot's Shipments list next to the slips: who Whatnot will ship that the app has no slip
 * for, and the other way round. Screen only; it changes nothing. */
function WhatnotCheck({ doc }: { doc: Doc }) {
  const w = doc.whatnot
  if (!w) {
    return (
      <p className="card small muted" data-testid="whatnot-check">
        Whatnot's shipment list has not been read for this show. After the show, open Seller Hub → Shipments on the laptop with Chat Scraper, pick this show, and press <b>Send shipments to Sales Ops</b> in the panel.
      </p>
    )
  }
  const read = `${count(w.shipments, 'shipment')} · read ${new Date(w.readAt).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
  if (w.lots > 0) {
    const clean = !w.lotsNotInApp && !w.salesNotOnWhatnot
    return (
      <div className={`card small ${clean ? '' : 'warn-card'}`} data-testid="whatnot-check">
        <b>Pick list from Whatnot</b> · {count(w.lots, 'lot')} in {read}
        <div>One row per Whatnot lot. The product is the one in the app, or "No Pick" with what the winner said in chat, for you to pick.</div>
        {clean && <div>Every lot matches a win in the app.</div>}
        {w.lotsNotInApp > 0 && (
          <div data-testid="lots-not-in-app">
            <b>{count(w.lotsNotInApp, 'lot')} on Whatnot with no win in the app</b> (marked "not in app"): check the show's chat for what was picked.
          </div>
        )}
        {w.salesNotOnWhatnot > 0 && (
          <div data-testid="sales-not-on-whatnot">
            <b>{count(w.salesNotOnWhatnot, 'win')} in the app that Whatnot does not list</b> (marked "not on Whatnot"): cancelled there, a misread win, or hidden by Whatnot's status filter. Don't pack them until checked.
          </div>
        )}
      </div>
    )
  }
  const off = doc.slips.filter((b) => b.whatnot && b.whatnot.items !== null && b.whatnot.items !== b.items)
  const clean = !w.missingHere.length && !w.notOnWhatnot.length && !off.length
  return (
    <div className={`card small ${clean ? '' : 'warn-card'}`} data-testid="whatnot-check">
      <b>Whatnot's shipment list</b> · {read}
      {clean && <div>Every buyer and item count matches the slips.</div>}
      {w.missingHere.length > 0 && (
        <div data-testid="missing-here">
          <b>On Whatnot, no slip here:</b> {w.missingHere.join(', ')}. A win the app never recorded: check the show's chat and Review.
        </div>
      )}
      {w.notOnWhatnot.length > 0 && (
        <div data-testid="not-on-whatnot">
          <b>Slip here, not on Whatnot:</b> {w.notOnWhatnot.join(', ')}. Cancelled on Whatnot, or a misread win. (Whatnot's status filter may also hide them.)
        </div>
      )}
      {off.length > 0 && (
        <div data-testid="count-differs">
          <b>Item counts differ:</b> {off.map((b) => `${b.username} (here ${b.items}, Whatnot ${b.whatnot!.items})`).join(', ')}.
        </div>
      )}
    </div>
  )
}

function Cover({ doc }: { doc: Doc }) {
  return (
    <div className="card slip slips-cover" data-testid="slips-cover">
      <h3 className="slips-cover-title">Order Slips</h3>
      <div>Auction Date: {sheetDate(doc.showDate)}</div>
      <div className="slips-totals" data-testid="slips-totals">
        <div>Total Buyers: {doc.totals.buyers}</div>
        <div>Total Items: {doc.totals.items}</div>
        <div>Total Gifts: {doc.totals.gifts}</div>
      </div>
      <table className="table slip-table slips-grid">
        <thead>
          <tr>
            <th>Username</th>
            <th>Total Items</th>
            <th>Total Gifts</th>
          </tr>
        </thead>
        <tbody>
          {doc.summary.map((b) => (
            <tr key={b.username}>
              <td>{b.username}</td>
              <td>{b.items}</td>
              <td>{b.gifts}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Slip({ b, date, showId, canEdit }: { b: BuyerSlip; date: string; showId: string; canEdit: boolean }) {
  return (
    <div className="card slip slips-page" data-testid={`slip-${b.username}`}>
      <h3 className="slips-buyer">{slipTitle(b)}</h3>
      <p className="no-print muted small">
        {b.orderId ? <Link to={`/app/orders/${b.orderId}`}>Order #{b.orderId}</Link> : 'Giveaway only'}
        {b.orderStatus && ` · ${b.orderStatus === 'paid' ? 'ready' : b.orderStatus === 'open' ? 'needs review' : b.orderStatus}`}
        {b.whatnot && (
          <span data-testid="slip-whatnot">
            {' · Whatnot: '}
            {b.whatnot.items === null ? 'listed' : count(b.whatnot.items, 'item')}
            {b.whatnot.statuses.length ? ` (${b.whatnot.statuses.join(', ')})` : ''}
            {b.whatnot.items !== null && b.whatnot.items !== b.items && <span className="badge warn"> differs</span>}
          </span>
        )}
      </p>
      <table className="table slip-table slips-grid">
        <thead>
          <tr>
            <th>Auction Date</th>
            <th>Auction Number</th>
            <th>Username</th>
            <th>Product Checklist</th>
            <th>Gift</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {b.rows.map((r, i) => (
            <Row key={i} r={r} date={date} buyer={b.username} showId={showId} canEdit={canEdit} />
          ))}
        </tbody>
      </table>
      <div className="slips-foot">
        <div className="slips-ready">💡 Ready To Ship 💡</div>
        <div>☐ Gift(s) included (if applicable)</div>
        <div>Packed by:</div>
        <div className="slips-sign" />
      </div>
    </div>
  )
}

function Row({ r, date, buyer, showId, canEdit }: { r: SlipRow; date: string; buyer: string; showId: string; canEdit: boolean }) {
  const qc = useQueryClient()
  const gifts = useMutation({
    mutationFn: (giftCount: number) => api('PATCH', `/api/orders/sales/${r.saleId}/gifts`, { giftCount }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['order-slips', showId] }),
  })
  const editable = canEdit && r.kind === 'sale' && r.saleId != null && !r.continued
  const label = r.giftCount ? `Gift ${r.giftCount}` : ''
  const notes = r.priceCents != null ? money(r.priceCents) : r.continued ? '-' : ''
  return (
    <tr className={r.giftCount ? 'has-gift' : undefined} data-testid="slip-row">
      <td>{date}</td>
      <td>
        {r.auction}
        {r.whatnotOrder && !r.continued && <div className="no-print muted small">Order #{r.whatnotOrder}</div>}
        {!r.inApp && <span className="badge warn no-print">not in app</span>}
        {!r.onWhatnot && <span className="badge danger no-print">not on Whatnot</span>}
      </td>
      <td>{buyer}</td>
      <td className={r.matched || r.kind === 'gift' ? undefined : 'unpicked'}>
        {r.product} <span className="tick" />
        {r.said.length > 0 && (
          <div className="slip-said small" data-testid="slip-said">
            Chat: {r.said.map((t) => `“${t}”`).join(' · ')}
          </div>
        )}
      </td>
      <td className="slip-gift">
        {editable ? (
          <>
            <select
              className="no-print"
              aria-label={`Gifts with ${r.auction}`}
              value={r.giftCount}
              disabled={gifts.isPending}
              onChange={(e) => gifts.mutate(Number(e.target.value))}
            >
              {[...new Set([...GIFT_CHOICES, r.giftCount])].map((n) => (
                <option key={n} value={n}>
                  {n ? `Gift ${n}` : '—'}
                </option>
              ))}
            </select>
            <span className="print-only">{label}</span>
          </>
        ) : (
          label
        )}
        {gifts.isError && <span className="form-error small no-print"> not saved</span>}
      </td>
      <td className="slip-price">{notes}</td>
    </tr>
  )
}
