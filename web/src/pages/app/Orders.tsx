import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { BuyerFlagChip } from '../../components/BuyerFlag'
import { ChangeProduct } from '../../components/ChangeProduct'
import { DeclineDialog } from '../../components/DeclineDialog'
import { Icon } from '../../components/Icon'
import { ProductPicker, type PickerTarget } from '../../components/ProductPicker'
import { ApiError, api, apiGet, money, type Order, type OrderStatus, type PickItem, type Sale, type SaleProduct, type SaleStatus, type Show } from '../../lib/api'
import { SHOP_TZ, fmtDate, fmtLongDate, fmtWhen, plural } from '../../lib/format'
import { ORDER_NEXT_STEP, ORDER_STEP_BACK, lotLabel, lotText, orderStatusLabel, packageStatusLabel, parcelStateLabel, saleStatusLabel, unknownWords } from '../../lib/labels'

const NEXT = ORDER_NEXT_STEP

/** Whatnot charges the winner at the win, so nothing waits for a payment: an order is ready to
 * pick at once. `open` only means an item still needs a person (not recognised); a declined
 * card shows on the order and in its own tab. */
const declined = (o: Order) => (o.sales ?? []).filter((s) => s.status === 'payment_failed')
/** Lots with no product yet (the order stays open until each one is named). */
const unnamed = (o: Order) => (o.sales ?? []).filter((s) => s.status === 'detected' && !s.sku)
/** The ones a person can name from here: a decision is still open on them. */
const unknown = (o: Order) => unnamed(o).filter((s) => s.review?.reason === 'unmatched_item')
const VIEWS: { key: string; label: string; match: (o: Order) => boolean }[] = [
  { key: 'queue', label: 'Ready to pick', match: (o) => o.status === 'paid' },
  { key: 'progress', label: 'In progress', match: (o) => o.status === 'picking' || o.status === 'packed' },
  { key: 'review', label: 'Needs review', match: (o) => o.status === 'open' },
  { key: 'declined', label: 'Payment declined', match: (o) => declined(o).length > 0 },
  { key: 'shipped', label: 'Shipped', match: (o) => o.status === 'shipped' },
  { key: 'all', label: 'All', match: () => true },
]
/** On the Shipped tab: where the parcels are, as Whatnot last said. */
const PARCEL_FILTERS: { key: string; label: string; match: (o: Order) => boolean }[] = [
  { key: '', label: 'All shipped', match: () => true },
  { key: 'carrier', label: 'Not with the carrier yet', match: (o) => o.parcel?.state === 'waiting' || o.parcel?.state === 'late' },
  { key: 'moving', label: 'On its way', match: (o) => o.parcel?.state === 'moving' },
  { key: 'failed', label: 'Delivery failed', match: (o) => o.parcel?.state === 'failed' },
  { key: 'delivered', label: 'Delivered', match: (o) => o.parcel?.state === 'delivered' },
]
const RANK: Record<OrderStatus, number> = { paid: 0, picking: 1, packed: 2, open: 3, shipped: 4, cancelled: 5 }
const STANDING: SaleStatus[] = ['reserved', 'paid', 'fulfilled', 'detected']

const titleOf = (s: Sale) => (s.sku ? `${s.sku.productName}${s.sku.name ? ' ' + s.sku.name : ''}` : s.itemTitle || s.item || '?')
/** A change to one sale's products: change one (which, when it holds several), or add one. */
interface ProductChange {
  sale: Sale
  product?: SaleProduct
  add?: boolean
}
/** Lots that are "the same item": one show, one title without the lot number. A title that is
 * only a lot number ("#520") names nothing, so such lots are never grouped. */
const groupKey = (s: Sale): string | null => {
  const title = (s.itemTitle || '').toLowerCase()
  return title ? `${s.showId}|${title}` : null
}
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ })

function wonAt(o: Order): number {
  return Math.max(0, ...(o.sales ?? []).map((s) => (s.wonAt ? Date.parse(s.wonAt) : s.paidAt ? Date.parse(s.paidAt) : 0)))
}

export function Orders() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const view = params.get('view') ?? 'queue'
  const showId = params.get('show') ?? ''
  const [find, setFind] = useState('')
  const [picking, setPicking] = useState<PickerTarget | null>(null)
  const [decline, setDecline] = useState<Sale | null>(null)
  const [changing, setChanging] = useState<ProductChange | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  const all = useQuery({ queryKey: ['orders', 'every', showId], queryFn: () => apiGet<{ orders: Order[]; truncated?: boolean }>(`/api/orders${showId ? '?show=' + showId : ''}`) })
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setParams(next)
  }
  const orders = (all.data?.orders ?? []).slice().sort((a, b) => RANK[a.status] - RANK[b.status] || wonAt(a) - wonAt(b) || a.id - b.id)
  const current = VIEWS.find((v) => v.key === view) ?? VIEWS[0]!
  const needle = find.trim().toLowerCase()
  const inView = orders.filter(current.match)
  // The Shipped tab can be narrowed to where the parcels are; it only shows once Whatnot says where any is.
  const tracked = view === 'shipped' && inView.some((o) => o.parcel)
  const parcelFilter = (tracked && PARCEL_FILTERS.find((f) => f.key === (params.get('parcel') ?? ''))) || PARCEL_FILTERS[0]!
  const visible = inView.filter(parcelFilter.match).filter((o) => !needle || o.buyer.toLowerCase().includes(needle))
  // "Print ready slips": every order that is ready to pick, by show (the slips are a per-show document).
  const readyByShow = new Map<number, { showDate: string; n: number }>()
  for (const o of orders) if (o.status === 'paid') readyByShow.set(o.showId, { showDate: o.showDate, n: (readyByShow.get(o.showId)?.n ?? 0) + 1 })

  // Lots nobody has named yet, grouped by show and title: "$1 BEAUTY PULLS #520" and "#521" are one group.
  const groups = new Map<string, Sale[]>()
  for (const o of orders)
    for (const s of unknown(o)) {
      const key = groupKey(s)
      if (key) groups.set(key, [...(groups.get(key) ?? []), s])
    }
  const biggest = [...groups.values()].sort((a, b) => b.length - a.length)[0]
  const stuck = biggest && biggest.length > 1 && can('review.act') ? biggest : null
  const stuckOrders = stuck ? new Set(stuck.map((s) => s.orderId)).size : 0

  const openPicker = (sale: Sale, applyToSimilar = false) => {
    if (!sale.review) return
    const key = groupKey(sale)
    const similar = (key ? (groups.get(key) ?? []) : []).filter((s) => s.id !== sale.id)
    setPicking({ sale, buyer: sale.buyer, showDate: sale.showDate, reviewId: sale.review.id, similar, applyToSimilar })
  }
  /** The dialog stays up until the list has refetched, so no button can act on a lot just settled. */
  const done = async (msg: string) => {
    await qc.invalidateQueries()
    setPicking(null)
    setDecline(null)
    setChanging(null)
    setToast(msg)
  }

  return (
    <section className="orders">
      <div className="page-head">
        <div>
          <h2>Orders</h2>
          <div className="sub">One order per buyer per show. Whatnot charges the winner at the win, so orders are ready to pick right away; the oldest win is first.</div>
        </div>
        <div className="head-actions">
          <select className="show-select" value={showId} onChange={(e) => set({ show: e.target.value })} aria-label="Show">
            <option value="">All shows</option>
            {shows.data?.shows.map((s) => (
              <option key={s.id} value={s.id}>
                {fmtLongDate(s.date)}
              </option>
            ))}
          </select>
          {showId && (
            <>
              <Link className="btn ghost" to={`/app/orders/slips/${showId}`}>
                <Icon name="print" size={16} />
                Order slips
              </Link>
              <Link className="btn ghost" to={`/app/orders/pick-list/${showId}`}>
                <Icon name="clipboard" size={16} />
                Pick list
              </Link>
              <Link className="btn ghost" to={`/app/orders/shipping/${showId}`} data-testid="shipping-link">
                Shipping
              </Link>
            </>
          )}
          <GiveawaysLink />
          <PrintReady byShow={readyByShow} />
        </div>
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist" style={{ marginBottom: 0, borderBottom: 0 }}>
          {VIEWS.map((v) => {
            const n = orders.filter(v.match).length
            return (
              <button key={v.key} role="tab" aria-selected={view === v.key} className={(view === v.key ? 'active' : '') + (n === 0 && v.key !== 'all' ? ' zero' : '')} onClick={() => set({ view: v.key })}>
                {v.label}
                {v.key !== 'all' ? <span className="tab-count">{n}</span> : null}
              </button>
            )
          })}
        </div>
        <label className="search">
          <Icon name="search" size={16} />
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a buyer" aria-label="Find a buyer" />
        </label>
      </div>
      {tracked && (
        <div className="parcel-filters" role="group" aria-label="Where the parcels are" data-testid="parcel-filters">
          {PARCEL_FILTERS.map((f) => {
            const n = inView.filter(f.match).length
            return (
              <button key={f.key} type="button" className={'chip-btn' + (parcelFilter.key === f.key ? ' on' : '')} aria-pressed={parcelFilter.key === f.key} onClick={() => set({ parcel: f.key })}>
                {f.label} <span className="tab-count">{n}</span>
              </button>
            )
          })}
        </div>
      )}
      {all.data?.truncated && (
        <p className="muted small" role="status" data-testid="orders-truncated">
          Showing the newest {all.data.orders.length} orders. Older ones are left out: pick a show above to see all of its orders.
        </p>
      )}
      {stuck && (
        <section className="banner" aria-label="Orders waiting on one unknown item" data-testid="stuck-banner">
          <div className="banner-main">
            <span className="banner-icon" aria-hidden>
              <Icon name="help" size={20} />
            </span>
            <div>
              <b>
                {plural(stuckOrders, 'order is', 'orders are')} waiting on one item the shop does not know yet.
              </b>
              <div className="small">
                The chat called it “{stuck[0]!.itemTitle} #…”. Name the product once and every one of these lots is settled.
              </div>
            </div>
          </div>
          <div className="btns">
            <button type="button" className="btn" onClick={() => set({ view: 'review', show: String(stuck[0]!.showId) })}>
              See them
            </button>
            <button type="button" className="btn primary" onClick={() => openPicker(stuck[0]!, true)} data-testid="name-it-once">
              Name it once
            </button>
          </div>
        </section>
      )}
      {all.isPending ? (
        <p className="muted">Loading…</p>
      ) : all.isError ? (
        <p className="form-error">Could not load orders.</p>
      ) : visible.length === 0 ? (
        <div className="empty">
          <b>{needle ? 'No buyer by that name here' : view === 'queue' ? 'Nothing to pick' : view === 'review' ? 'Nothing needs review' : view === 'declined' ? 'No declined payments' : 'No orders here'}</b>
          {needle ? '' : view === 'queue' ? 'Orders arrive here as soon as a buyer wins.' : view === 'declined' ? 'A card Whatnot declines after a win shows here. The item goes back in stock unless picking had started.' : ''}
        </div>
      ) : (
        <div className="card flush">
          <table className="table" data-testid="orders">
            <thead>
              <tr>
                <th>Buyer</th>
                <th>Items</th>
                <th className="num">Total</th>
                <th className="col-show">Show</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Next step</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((o) => (
                <tr key={o.id} data-testid={`order-${o.buyer}`}>
                  <td className="c-buyer">
                    <Link to={`/app/orders/${o.id}`} className="buyer">
                      {o.buyer}
                    </Link>
                    <BuyerFlagChip name={o.buyer} />
                    <span className="show-inline">{fmtDate(o.showDate)}</span>
                  </td>
                  <td className="c-items">
                    <Items o={o} />
                  </td>
                  <td className="num c-total">
                    <b>{money(o.totalCents)}</b>
                  </td>
                  <td className="muted col-show">{fmtDate(o.showDate)}</td>
                  <td className="c-status">
                    <Status o={o} />
                  </td>
                  <td className="c-action">
                    <Actions o={o} onPick={openPicker} onDecline={setDecline} onChange={setChanging} onRemoved={done} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!all.isPending && !all.isError && visible.length > 0 && (
        <div className="small faint foot">
          <span>
            {plural(visible.length, 'order', 'orders')}
            {visible.length !== orders.length ? ` of ${orders.length}` : ''} · oldest win first
          </span>
        </div>
      )}
      {picking && <ProductPicker target={picking} onClose={() => setPicking(null)} onDone={done} />}
      {changing && <ChangeProduct sale={changing.sale} buyer={changing.sale.buyer} showDate={changing.sale.showDate} product={changing.product} add={changing.add} onClose={() => setChanging(null)} onDone={done} />}
      {decline && decline.review && <DeclineDialog sale={decline} buyer={decline.buyer} showDate={decline.showDate} reviewId={decline.review.id} onClose={() => setDecline(null)} onDone={done} />}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

/** "10 × $1 Beauty Pulls" with the lot numbers under it, one line per product. */
function Items({ o }: { o: Order }) {
  const groups = new Map<string, { title: string; qty: number; lots: number[]; unknown: boolean }>()
  for (const s of (o.sales ?? []).filter((s) => STANDING.includes(s.status))) {
    // One line per product: a win with two products ("two for one") counts under both.
    const parts = (s.products ?? []).length > 0 ? s.products!.map((p) => ({ title: `${p.sku.productName}${p.sku.name ? ' ' + p.sku.name : ''}`, qty: p.quantity })) : [{ title: titleOf(s), qty: s.quantity }]
    for (const part of parts) {
      const g = groups.get(part.title) ?? { title: part.title, qty: 0, lots: [], unknown: !s.sku }
      g.qty += part.qty
      if (s.lot != null) g.lots.push(s.lot)
      groups.set(part.title, g)
    }
  }
  return (
    <div className="item-lines">
      {[...groups.values()].map((g) => (
        <div key={g.title} className="item-line">
          <div>
            <b>{g.qty} ×</b> {g.unknown ? <span className="unknown">“{g.title}”</span> : g.title}
          </div>
          {g.lots.length > 0 && <Lots lots={g.lots} />}
        </div>
      ))}
    </div>
  )
}

function Lots({ lots }: { lots: number[] }) {
  const shown = lots.slice(0, 6)
  return (
    <span className="lots">
      {shown.map((n, i) => (
        <span className="lot" key={i}>
          #{n}
        </span>
      ))}
      {lots.length > shown.length && <span className="lot more">+{lots.length - shown.length} more</span>}
    </span>
  )
}

function Status({ o }: { o: Order }) {
  const unk = unnamed(o)
  const dec = declined(o)
  const won = wonAt(o)
  return (
    <>
      <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
      {o.status === 'open' && <div className="small sub unknown">{unk.length ? unknownWords(unk) : 'older sale, waiting for the payment file'}</div>}
      {o.status === 'paid' && won > 0 && <div className="small faint sub">won {time(new Date(won).toISOString())}</div>}
      {(o.sales ?? []).some((s) => s.review?.reason === 'lot_won_twice') && (
        <div className="small sub unknown" data-testid={`lot-won-twice-${o.buyer}`}>
          a lot here has two winners, see Review
        </div>
      )}
      {(o.sales ?? []).some((s) => s.review?.reason === 'decline_seen') && (
        <div className="small sub unknown" data-testid={`decline-seen-${o.buyer}`}>
          card declined on the stream, waiting for Whatnot
        </div>
      )}
      {o.status === 'picking' && o.pickedAt && <div className="small faint sub">since {time(o.pickedAt)}</div>}
      {o.status === 'packed' && (
        <div className="small faint sub">
          {o.packedBy ? `by ${o.packedBy}` : 'packed'}
          {o.packedAt ? ` · ${time(o.packedAt)}` : ''}
        </div>
      )}
      {dec.length > 0 && (
        <div className="small sub" data-testid={`declined-${o.buyer}`}>
          <span className="status payment_failed">{saleStatusLabel('payment_failed')}</span> {dec.map((s) => `${s.quantity}× ${s.sku ? s.sku.code : s.item || '?'}`).join(', ')}
        </div>
      )}
      {o.parcel ? <Parcel o={o} /> : o.tracking && <div className="muted small sub">{o.tracking}</div>}
    </>
  )
}

/** Where a shipped order's parcel is, as Whatnot last said: one phrase, then each parcel's carrier and tracking number. */
function Parcel({ o }: { o: Order }) {
  const p = o.parcel!
  const many = p.parcels.length > 1
  return (
    <div className="small sub" data-testid={`parcel-${o.buyer}`}>
      <span className={p.state === 'failed' || p.state === 'late' ? 'form-error' : ''}>{parcelStateLabel(p.state)}</span>
      <span className="faint"> · {fmtWhen(p.changedAt)}</span>
      {p.parcels.map((x, i) => (
        <div key={i} className="muted">
          {many ? `${packageStatusLabel(x.status)} · ` : ''}
          {[x.carrier, x.tracking].filter(Boolean).join(' ') || (many ? 'no tracking number yet' : '')}
        </div>
      ))}
    </div>
  )
}

/** One verb for the order's next step, and a menu for the rest. */
function Actions({ o, onPick, onDecline, onChange, onRemoved }: { o: Order; onPick: (sale: Sale) => void; onDecline: (sale: Sale) => void; onChange: (c: ProductChange) => void; onRemoved: (msg: string) => void | Promise<void> }) {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const move = useMutation({
    mutationFn: (to: OrderStatus) => api('POST', `/api/orders/${o.id}/status`, { status: to }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  const canReview = can('review.act')
  const canFulfil = can('orders.fulfill')
  // Picking, packing and shipping need every lot paid; a decline mid-fulfilment takes that away.
  const next = o.verified ? NEXT[o.status] : undefined
  const back = o.verified ? ORDER_STEP_BACK[o.status] : undefined
  const unk = unknown(o)
  const dec = declined(o).filter((s) => s.review?.reason === 'payment_declined')
  let primary: ReactNode = null
  let declineInMenu = dec.length > 0 && canReview
  if (o.status === 'open' && unk.length > 0 && canReview) {
    primary = (
      <button type="button" className="btn small primary" onClick={() => onPick(unk[0]!)} data-testid="name-product">
        Name the product
      </button>
    )
  } else if (next && canFulfil) {
    primary = (
      <button type="button" className="btn small primary" onClick={() => move.mutate(next.to)} disabled={move.isPending}>
        {next.label}
      </button>
    )
  } else if (declineInMenu) {
    primary = (
      <button type="button" className="btn small" onClick={() => onDecline(dec[0]!)} data-testid="resolve-decline">
        Resolve decline
      </button>
    )
    declineInMenu = false
  }
  const more: ReactNode[] = [
    <Link key="open" role="menuitem" to={`/app/orders/${o.id}`}>
      <Icon name="print" size={16} />
      Open the order and slip
    </Link>,
  ]
  if (declineInMenu)
    more.push(
      <button key="decline" type="button" role="menuitem" onClick={() => onDecline(dec[0]!)}>
        <Icon name="card" size={16} />
        Resolve the declined payment
      </button>,
    )
  // A sale that stands, has a product and has not shipped can get another one. One menu
  // entry per such sale: a buyer's order often holds several lots.
  if (canReview && o.status !== 'shipped' && o.status !== 'cancelled')
    for (const s of o.sales ?? []) {
      if (!s.sku && s.status === 'detected' && s.saleMode === 'winners_pick') {
        // Still waiting for the winner's pick: a "two for one" nobody typed in chat.
        const two = (s.productsAllowed ?? 1) > 1
        more.push(
          <button
            key={`allow-${s.id}`}
            type="button"
            role="menuitem"
            onClick={async () => {
              try {
                await api('PATCH', `/api/orders/sales/${s.id}/allowed`, { products: two ? 1 : 2 })
                await onRemoved(two ? `${o.buyer}'s ${lotText(s)} takes one product again.` : `${o.buyer}'s ${lotText(s)} is a two for one: it takes two of the winner's spots.`)
              } catch (e) {
                setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
              }
            }}
          >
            <Icon name={two ? 'minus' : 'plus'} size={16} />
            {two ? `Back to one product for ${lotText(s)}` : `Two for one: ${lotText(s)} takes 2 products`}
          </button>,
        )
      }
      if (!s.sku || (s.status !== 'paid' && s.status !== 'reserved')) continue
      const several = (s.products ?? []).length > 1
      // One product: change it. Several ("two for one"): change or take off each by name.
      for (const p of several ? s.products! : [undefined])
        more.push(
          <button key={`change-${s.id}-${p?.rowId ?? 0}`} type="button" role="menuitem" onClick={() => onChange({ sale: s, product: p })} data-testid={`change-product-${s.id}`}>
            <Icon name="undo" size={16} />
            {p ? `Change ${p.sku.productName} of ${lotText(s)}` : `Change the product of ${lotText(s)}`}
          </button>,
        )
      if (several)
        for (const p of s.products!)
          more.push(
            <button
              key={`off-${s.id}-${p.rowId}`}
              type="button"
              role="menuitem"
              onClick={async () => {
                try {
                  await api('DELETE', `/api/orders/sales/${s.id}/products/${p.rowId}`)
                  await onRemoved(`${p.sku.productName} is off ${o.buyer}'s ${lotText(s)} and back in stock.`)
                } catch (e) {
                  setError(e instanceof ApiError ? e.message : 'Could not take it off. Try again.')
                }
              }}
            >
              <Icon name="minus" size={16} />
              Take {p.sku.productName} off {lotText(s)}
            </button>,
          )
      more.push(
        <button key={`add-${s.id}`} type="button" role="menuitem" onClick={() => onChange({ sale: s, add: true })} data-testid={`add-product-${s.id}`}>
          <Icon name="plus" size={16} />
          Add a product to {lotText(s)}
        </button>,
      )
    }
  if (o.status === 'open' && canReview)
    more.push(
      <Link key="review" role="menuitem" to={`/app/review/${o.showId}`}>
        <Icon name="review" size={16} />
        Open Review for this show
      </Link>,
    )
  if (back && canFulfil)
    more.push(
      <hr key="hr" />,
      <button key="back" type="button" role="menuitem" onClick={() => move.mutate(back.to)}>
        <Icon name="undo" size={16} />
        {back.label}
      </button>,
    )
  return (
    <>
      <div className="acts">
        {primary}
        <Menu label={`More for ${o.buyer}`}>{more}</Menu>
      </div>
      {error && <div className="form-error small">{error}</div>}
    </>
  )
}

/** Opens the show's order slips for the orders that are ready to pick, ready to print. The slips
 * are a per-show document, so with ready orders in several shows the button lists each show. */
function PrintReady({ byShow }: { byShow: Map<number, { showDate: string; n: number }> }) {
  const shows = [...byShow.entries()].sort((a, b) => b[1].showDate.localeCompare(a[1].showDate) || b[0] - a[0])
  const total = shows.reduce((n, [, s]) => n + s.n, 0)
  const label = total ? `Print ${plural(total, 'ready slip', 'ready slips')}` : 'Print ready slips'
  const to = (id: number) => `/app/orders/slips/${id}?only=ready`
  if (total === 0)
    return (
      <button type="button" className="btn primary" disabled data-testid="print-ready">
        <Icon name="print" size={16} />
        {label}
      </button>
    )
  if (shows.length === 1)
    return (
      <Link className="btn primary" to={to(shows[0]![0])} data-testid="print-ready">
        <Icon name="print" size={16} />
        {label}
      </Link>
    )
  return (
    <Menu
      label="Print ready slips, by show"
      className="btn primary"
      testId="print-ready"
      trigger={
        <>
          <Icon name="print" size={16} />
          {label}
          <Icon name="chevron" size={16} />
        </>
      }
    >
      {shows.map(([id, s]) => (
        <Link key={id} role="menuitem" to={to(id)}>
          <Icon name="print" size={16} />
          {fmtLongDate(s.showDate)} · {plural(s.n, 'order', 'orders')}
        </Link>
      ))}
    </Menu>
  )
}

/** A button and the menu under it: the row's "more" dots by default, or any `trigger` given. */
function Menu({ label, children, trigger, className = 'icon-btn sm', testId }: { label: string; children: ReactNode; trigger?: ReactNode; className?: string; testId?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className={className} aria-label={trigger ? undefined : label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} data-testid={testId}>
        {trigger ?? <Icon name="more" size={18} />}
      </button>
      {open && (
        <div className="menu" role="menu" aria-label={label} onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  )
}

export function OrderDetail() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['order', id], queryFn: () => apiGet<{ order: Order }>(`/api/orders/${id}`) })
  const [tracking, setTracking] = useState('')
  const [notes, setNotes] = useState<string | null>(null)
  const [packedBy, setPackedBy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [changing, setChanging] = useState<ProductChange | null>(null)
  const [changed, setChanged] = useState<string | null>(null)
  const status = useMutation({
    mutationFn: (to: OrderStatus) => api('POST', `/api/orders/${id}/status`, { status: to, tracking: tracking || undefined, packedBy: packedBy ?? undefined }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  const save = useMutation({
    mutationFn: () => api('PATCH', `/api/orders/${id}`, { notes, tracking, ...(packedBy !== null ? { packedBy } : {}) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['order', id] }),
  })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the order.</p>
  const o = q.data.order
  const next = NEXT[o.status]
  return (
    <section>
      <p>
        <Link to="/app/orders">← Orders</Link>
      </p>
      <div className="product-head">
        <h2>
          {o.buyer} · {o.showDate}
          <BuyerFlagChip name={o.buyer} />
        </h2>
        <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
      </div>
      <p className="small no-print">
        <Link to={`/app/buyers/${encodeURIComponent(o.buyer)}`}>About {o.buyer}: everything they have bought, and the team's notes</Link>
      </p>
      <PackingSlip o={o} />
      <OrderProducts o={o} onChange={setChanging} />
      {changed && (
        <p className="card small no-print" role="status" data-testid="order-changed">
          {changed}
        </p>
      )}
      {changing && (
        <ChangeProduct
          sale={changing.sale}
          buyer={o.buyer}
          showDate={o.showDate}
          product={changing.product}
          add={changing.add}
          onClose={() => setChanging(null)}
          onDone={async (msg) => {
            await qc.invalidateQueries()
            setChanging(null)
            setChanged(msg)
          }}
        />
      )}
      {o.parcel && (
        <div className="card no-print" data-testid="order-parcel">
          <b>Where the parcel is</b>
          <Parcel o={o} />
        </div>
      )}
      {can('orders.fulfill') && (
        <div className="card no-print">
          <label className="field">
            <span>Tracking number</span>
            <input value={tracking || o.tracking || ''} onChange={(e) => setTracking(e.target.value)} />
          </label>
          <label className="field">
            <span>Packed by (name or initials)</span>
            <input value={packedBy ?? o.packedBy ?? ''} onChange={(e) => setPackedBy(e.target.value)} maxLength={80} />
          </label>
          <label className="field">
            <span>Notes</span>
            <textarea value={notes ?? o.notes ?? ''} onChange={(e) => setNotes(e.target.value)} rows={2} />
          </label>
          <div className="row-actions">
            <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>
              Save
            </button>
            {next && (
              <button className="btn primary" onClick={() => status.mutate(next.to)} disabled={status.isPending} data-testid="advance">
                {next.label}
              </button>
            )}
            {o.status === 'picking' && (
              <button className="btn" onClick={() => status.mutate('paid')}>
                Back to queue
              </button>
            )}
          </div>
          {error && <p className="form-error">{error}</p>}
        </div>
      )}
      <BuyerHistory buyer={o.buyer} current={o.id} />
    </section>
  )
}

/** On the order page: each product of each standing sale, with the way to change it. Never printed. */
function OrderProducts({ o, onChange }: { o: Order; onChange: (c: ProductChange) => void }) {
  const { can } = useAuth()
  if (!can('review.act') || o.status === 'shipped' || o.status === 'cancelled') return null
  const sales = (o.sales ?? []).filter((s) => s.sku && (s.status === 'paid' || s.status === 'reserved'))
  if (sales.length === 0) return null
  return (
    <div className="card no-print" data-testid="order-products">
      <b>Products on this order</b>
      <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0', display: 'grid', gap: 6 }}>
        {sales.map((s) =>
          (s.products && s.products.length > 0 ? s.products : [undefined]).map((p) => {
            const sku = p?.sku ?? s.sku!
            const name = `${sku.productName} ${sku.name}`.trim()
            return (
              <li key={`${s.id}-${p?.rowId ?? 0}`} className="row-actions">
                <span>
                  {lotText(s)} · <b>{p ? `${p.quantity} × ` : ''}{name}</b>
                </span>
                <button type="button" className="btn small" onClick={() => onChange({ sale: s, product: p })} aria-label={`Change ${name} of ${lotText(s)}`}>
                  Change the product
                </button>
              </li>
            )
          }),
        )}
      </ul>
      <span className="muted small">The old product goes back to stock and the new one leaves it. The price the buyer paid does not change.</span>
    </div>
  )
}

const NOT_SHIPPING: SaleStatus[] = ['cancelled', 'payment_failed']

/** One empty tick box per unit, so a packer can tick each physical item like on the paper sheet. */
function Ticks({ n }: { n: number }) {
  const boxes = Math.min(n, 24)
  return (
    <span className="ticks" aria-label={`${n} to pack`}>
      {Array.from({ length: boxes }, (_, i) => (
        <span className="tick" data-testid="tick" key={i} />
      ))}
      {n > boxes && <span className="muted small"> ×{n}</span>}
    </span>
  )
}

/**
 * The shop's per-buyer packing sheet: header "Username: <buyer> - <N> Items", a row per
 * auction with a tick per unit, gift lines, and the "Ready to ship / Gift(s) included /
 * Packed by / date" footer. Whatnot's auction number is not in chat, so the Auction column
 * shows the auction type (or "Listing", as on the sheet) and the chat line it came from.
 */
function PackingSlip({ o }: { o: Order }) {
  const shipping = (o.sales ?? []).filter((s) => !NOT_SHIPPING.includes(s.status))
  const dropped = (o.sales ?? []).filter((s) => NOT_SHIPPING.includes(s.status))
  const gifts = o.gifts ?? []
  const date = fmtLongDate(o.showDate)
  const count = o.items + gifts.length // the sheet counts gift lines among the items
  const extras = shipping.reduce((n, s) => n + s.giftCount, 0) // "Gift N" lots: free gifts in the box
  const label = (s: Sale) => `${lotLabel(s.auctionType) || 'Listing'}${s.seq ? ` · line ${s.seq}` : ''}`
  return (
    <div className="card slip" data-testid="packing-slip">
      <div className="slip-head">
        <div className="slip-title" data-testid="slip-title">
          Username: <b>{o.buyer}</b> – {count} Item{count === 1 ? '' : 's'}
          {extras > 0 && ` + ${extras} Gift${extras === 1 ? '' : 's'}`}
        </div>
        <div className="muted small">
          Auction date {date} · order #{o.id}
          {o.status === 'open' && <b className="no-print"> · an item still needs a decision in Review</b>}
        </div>
      </div>
      <table className="table slip-table">
        <thead>
          <tr>
            <th>Auction date</th>
            <th>Auction</th>
            <th>Username</th>
            <th>Product checklist</th>
            <th>Gift</th>
            <th className="no-print">Payment</th>
            <th className="slip-notes">Notes</th>
          </tr>
        </thead>
        <tbody>
          {shipping.map((s) => (
            <tr key={s.id} data-testid={`slip-sale-${s.id}`}>
              <td className="muted">{o.showDate}</td>
              <td>{label(s)}</td>
              <td>{o.buyer}</td>
              <td>
                <Ticks n={s.quantity} /> {s.sku ? `${s.sku.productName} ${s.sku.name}`.trim() : s.item}
                {s.sku ? <code className="sku">{s.sku.code}</code> : <span className="badge warn">unmatched</span>}
                {s.quantity > 1 && <span className="muted small"> × {s.quantity}</span>}
              </td>
              <td>{s.giftCount ? `Gift ${s.giftCount}` : ''}</td>
              <td className="no-print">
                <span className={`status ${s.status}`}>{saleStatusLabel(s.status)}</span>
                {s.externalOrderId && <span className="muted small"> · #{s.externalOrderId}</span>}
              </td>
              <td className="slip-notes" />
            </tr>
          ))}
          {gifts.map((g, i) => (
            <tr key={`gift-${i}`} className="gift" data-testid="slip-gift">
              <td className="muted">{o.showDate}</td>
              <td>Gift{g.seq ? ` · line ${g.seq}` : ''}</td>
              <td>{o.buyer}</td>
              <td>
                <Ticks n={1} /> {g.item || 'Gift'}
              </td>
              <td>✓</td>
              <td className="no-print muted small">gift · no charge</td>
              <td className="slip-notes" />
            </tr>
          ))}
        </tbody>
      </table>
      {dropped.length > 0 && (
        <p className="muted small no-print">
          Not shipping: {dropped.map((s) => `${s.sku ? s.sku.productName : s.item} (${s.status === 'payment_failed' ? 'payment declined' : s.status})`).join(', ')}
        </p>
      )}
      {o.notes && (
        <p className="slip-order-notes">
          <b>Notes:</b> {o.notes}
        </p>
      )}
      <div className="slip-foot" data-testid="slip-foot">
        <span>
          <span className={'tick' + (o.status === 'shipped' ? ' on' : '')} /> Ready to ship
        </span>
        <span>
          <span className={'tick' + (gifts.length || extras ? ' on' : '')} /> Gift(s) included (if applicable)
        </span>
        <span>
          Packed by: {o.packedBy ? <b>{o.packedBy}</b> : <span className="blank" />}
        </span>
        <span>
          Date: {o.packedAt ? new Date(o.packedAt).toLocaleDateString() : <span className="blank" />}
        </span>
      </div>
      <p className="muted small no-print">
        {o.items} item{o.items === 1 ? '' : 's'} · {money(o.totalCents)}
      </p>
      <button className="btn small" onClick={() => window.print()}>
        Print packing slip
      </button>
    </div>
  )
}

function BuyerHistory({ buyer, current }: { buyer: string; current: number }) {
  const q = useQuery({ queryKey: ['buyer', buyer], queryFn: () => apiGet<{ orders: Order[] }>(`/api/orders/buyers/${encodeURIComponent(buyer)}`) })
  const others = (q.data?.orders ?? []).filter((o) => o.id !== current)
  if (!others.length) return null
  return (
    <>
      <h3>{buyer} in other shows</h3>
      <table className="table small">
        <thead>
          <tr>
            <th>Show</th>
            <th className="num">Items</th>
            <th>Total</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {others.map((o) => (
            <tr key={o.id}>
              <td>
                <Link to={`/app/orders/${o.id}`}>{o.showDate}</Link>
              </td>
              <td className="num">{o.items}</td>
              <td>{money(o.totalCents)}</td>
              <td>
                <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** The way to "Giveaways to assign", with how many wait; hidden until Whatnot lists one. */
function GiveawaysLink() {
  const q = useQuery({ queryKey: ['giveaways'], queryFn: () => apiGet<{ giveaways: unknown[]; waiting: number }>('/api/giveaways') })
  if (!q.data?.giveaways?.length) return null
  return (
    <Link className="btn ghost" to="/app/orders/giveaways" data-testid="giveaways-link">
      <Icon name="gift" size={16} />
      Giveaways
      {q.data.waiting > 0 && <span className="tab-count">{q.data.waiting}</span>}
    </Link>
  )
}

export function PickList() {
  const { showId = '' } = useParams()
  const q = useQuery({ queryKey: ['pick-list', showId], queryFn: () => apiGet<{ items: PickItem[] }>(`/api/orders/shows/${showId}/pick-list`) })
  return (
    <section>
      <p>
        <Link to={`/app/orders?view=queue&show=${showId}`}>← Orders</Link>
      </p>
      <h2>Pick list</h2>
      <p className="muted small">Quantities to pull for every paid order of this show that has not shipped.</p>
      {q.data && (
        <table className="table" data-testid="pick-list">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Product</th>
              <th className="num">Quantity</th>
              <th className="num">Orders</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((i) => (
              <tr key={i.skuId}>
                <td>
                  <code>{i.code}</code>
                </td>
                <td>
                  {i.product} {i.variant}
                </td>
                <td className="num">
                  <b>{i.quantity}</b>
                </td>
                <td className="num">{i.orders}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button className="btn small" onClick={() => window.print()}>
        Print
      </button>
    </section>
  )
}
