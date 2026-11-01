import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { ApiError, api, apiGet, money, type Order, type OrderStatus, type PickItem, type Sale, type SaleStatus, type Show } from '../../lib/api'
import { fmtLongDate } from '../../lib/format'

const NEXT: Partial<Record<OrderStatus, { label: string; to: OrderStatus }>> = {
  paid: { label: 'Start picking', to: 'picking' },
  picking: { label: 'Packed', to: 'packed' },
  packed: { label: 'Shipped', to: 'shipped' },
}

/** Whatnot charges the winner at the win, so nothing waits for a payment: an order is ready to
 * pick at once. `open` only means an item still needs a person (not recognised); a declined
 * card shows on the order and in its own tab. */
const declined = (o: Order) => (o.sales ?? []).filter((s) => s.status === 'payment_failed')
const VIEWS: { key: string; label: string; match: (o: Order) => boolean }[] = [
  { key: 'queue', label: 'Ready to pick', match: (o) => o.status === 'paid' },
  { key: 'progress', label: 'In progress', match: (o) => o.status === 'picking' || o.status === 'packed' },
  { key: 'review', label: 'Needs review', match: (o) => o.status === 'open' },
  { key: 'declined', label: 'Payment declined', match: (o) => declined(o).length > 0 },
  { key: 'shipped', label: 'Shipped', match: (o) => o.status === 'shipped' },
  { key: 'all', label: 'All', match: () => true },
]
const STATUS_LABEL: Partial<Record<OrderStatus, string>> = { paid: 'ready', open: 'needs review' }
const RANK: Record<OrderStatus, number> = { paid: 0, picking: 1, packed: 2, open: 3, shipped: 4, cancelled: 5 }

function itemSummary(o: Order): string {
  const parts = (o.sales ?? [])
    .filter((s) => ['reserved', 'paid', 'fulfilled', 'detected'].includes(s.status))
    .map((s) => `${s.quantity}× ${s.sku ? s.sku.code : s.item || '?'}`)
  return parts.join(', ')
}

function paidAt(o: Order): number {
  return Math.max(0, ...(o.sales ?? []).map((s) => (s.paidAt ? Date.parse(s.paidAt) : 0)))
}

export function Orders() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const view = params.get('view') ?? 'queue'
  const showId = params.get('show') ?? ''
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  const q = useQuery({ queryKey: ['orders', 'all', showId], queryFn: () => apiGet<{ orders: Order[] }>(`/api/orders?status=queue${showId ? '&show=' + showId : ''}`) })
  const all = useQuery({ queryKey: ['orders', 'every', showId], queryFn: () => apiGet<{ orders: Order[] }>(`/api/orders${showId ? '?show=' + showId : ''}`) })
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next)
  }
  const orders = (all.data?.orders ?? []).slice().sort((a, b) => RANK[a.status] - RANK[b.status] || paidAt(a) - paidAt(b) || a.id - b.id)
  const current = VIEWS.find((v) => v.key === view) ?? VIEWS[0]!
  const visible = orders.filter(current.match)
  void q
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Orders</h2>
          <div className="sub">One order per buyer per show. Whatnot charges the winner at the win, so orders are ready to pick right away; the oldest win is first.</div>
        </div>
        {showId && (
          <Link className="btn ghost" to={`/app/orders/pick-list/${showId}`}>
            Pick list for this show
          </Link>
        )}
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist" style={{ marginBottom: 0, borderBottom: 0 }}>
          {VIEWS.map((v) => {
            const n = orders.filter(v.match).length
            return (
              <button key={v.key} role="tab" aria-selected={view === v.key} className={view === v.key ? 'active' : ''} onClick={() => set('view', v.key)}>
                {v.label}
                {v.key !== 'all' ? <span className="tab-count">{n}</span> : null}
              </button>
            )
          })}
        </div>
        <select value={showId} onChange={(e) => set('show', e.target.value)} aria-label="Show">
          <option value="">All shows</option>
          {shows.data?.shows.map((s) => (
            <option key={s.id} value={s.id}>
              {s.date}
            </option>
          ))}
        </select>
      </div>
      {all.isPending ? (
        <p className="muted">Loading…</p>
      ) : all.isError ? (
        <p className="form-error">Could not load orders.</p>
      ) : visible.length === 0 ? (
        <div className="empty">
          <b>{view === 'queue' ? 'Nothing to pick' : view === 'review' ? 'Nothing needs review' : view === 'declined' ? 'No declined payments' : 'No orders here'}</b>
          {view === 'queue' ? 'Orders arrive here as soon as a buyer wins.' : view === 'declined' ? 'A card Whatnot declines after a win shows here. The item goes back in stock unless picking had started.' : ''}
        </div>
      ) : (
        <table className="table" data-testid="orders">
          <thead>
            <tr>
              <th>Buyer</th>
              <th>Items</th>
              <th className="num">Total</th>
              <th>Show</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((o) => (
              <tr key={o.id} data-testid={`order-${o.buyer}`}>
                <td>
                  <Link to={`/app/orders/${o.id}`}>
                    <b>{o.buyer}</b>
                  </Link>
                </td>
                <td className="order-items">
                  <b>{o.items}</b> · {itemSummary(o)}
                </td>
                <td className="num">{money(o.totalCents)}</td>
                <td className="muted">{o.showDate}</td>
                <td>
                  <span className={`status ${o.status}`}>{STATUS_LABEL[o.status] ?? o.status}</span>
                  {o.status === 'open' && (
                    <div className="muted small">
                      {(o.sales ?? []).some((s) => s.status === 'detected') ? (
                        <Link to="/app/review">item not recognised — see Review</Link>
                      ) : (
                        'older sale, waiting for the payment file'
                      )}
                    </div>
                  )}
                  {declined(o).length > 0 && (
                    <div className="small" data-testid={`declined-${o.buyer}`}>
                      <span className="status payment_failed">payment declined</span> {declined(o).map((s) => `${s.quantity}× ${s.sku ? s.sku.code : s.item || '?'}`).join(', ')}
                    </div>
                  )}
                  {o.tracking && <div className="muted small">{o.tracking}</div>}
                </td>
                <td>{can('orders.fulfill') && <Advance order={o} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function Advance({ order }: { order: Order }) {
  const qc = useQueryClient()
  const next = NEXT[order.status]
  const [error, setError] = useState<string | null>(null)
  const m = useMutation({
    mutationFn: () => api('POST', `/api/orders/${order.id}/status`, { status: next!.to }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  if (!next) return null
  return (
    <>
      <button className="btn small primary" onClick={() => m.mutate()} disabled={m.isPending}>
        {next.label}
      </button>
      {error && <div className="form-error small">{error}</div>}
    </>
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
        </h2>
        <span className={`status ${o.status}`}>{STATUS_LABEL[o.status] ?? o.status}</span>
      </div>
      <PackingSlip o={o} />
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

const TYPE_LABEL: Record<string, string> = { red: 'Red', bundle: 'Bundle', ultra: 'Ultra', mega: 'Mega', quick_hit: 'Quick Hit' }
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
  const label = (s: Sale) => `${TYPE_LABEL[s.auctionType] ?? 'Listing'}${s.seq ? ` · line ${s.seq}` : ''}`
  return (
    <div className="card slip" data-testid="packing-slip">
      <div className="slip-head">
        <div className="slip-title" data-testid="slip-title">
          Username: <b>{o.buyer}</b> – {count} Item{count === 1 ? '' : 's'}
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
              <td />
              <td className="no-print">
                <span className={`status ${s.status}`}>{s.status.replace('_', ' ')}</span>
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
          <span className={'tick' + (gifts.length ? ' on' : '')} /> Gift(s) included (if applicable)
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
                <span className={`status ${o.status}`}>{o.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
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
