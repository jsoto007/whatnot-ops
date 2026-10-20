import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { ApiError, api, apiGet, money, type Order, type OrderStatus, type PickItem, type Show } from '../../lib/api'

const NEXT: Partial<Record<OrderStatus, { label: string; to: OrderStatus }>> = {
  paid: { label: 'Start picking', to: 'picking' },
  picking: { label: 'Packed', to: 'packed' },
  packed: { label: 'Shipped', to: 'shipped' },
}

export function Orders() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const view = params.get('view') ?? 'queue'
  const showId = params.get('show') ?? ''
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  const q = useQuery({
    queryKey: ['orders', view, showId],
    queryFn: () => apiGet<{ orders: Order[] }>(`/api/orders?status=${view === 'all' ? 'all' : view}${showId ? '&show=' + showId : ''}`),
  })
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next)
  }
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Orders</h2>
          <div className="sub">One order per buyer per show · pick, pack and ship the verified ones</div>
        </div>
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {[
            ['queue', 'To fulfil'],
            ['open', 'Awaiting payment'],
            ['shipped', 'Shipped'],
            ['all', 'All'],
          ].map(([k, label]) => (
            <button key={k} role="tab" aria-selected={view === k} className={view === k ? 'active' : ''} onClick={() => set('view', k)}>
              {label}
            </button>
          ))}
        </div>
        <select value={showId} onChange={(e) => set('show', e.target.value)} aria-label="Show">
          <option value="">All shows</option>
          {shows.data?.shows.map((s) => (
            <option key={s.id} value={s.id}>
              {s.date}
            </option>
          ))}
        </select>
        {showId && (
          <Link className="btn small" to={`/app/orders/pick-list/${showId}`}>
            Pick list
          </Link>
        )}
      </div>
      {q.isPending ? (
        <p className="muted">Loading…</p>
      ) : q.isError ? (
        <p className="form-error">Could not load orders.</p>
      ) : q.data.orders.length === 0 ? (
        <div className="empty">
          <b>{view === 'queue' ? 'Nothing to fulfil' : 'No orders here'}</b>
          {view === 'queue' ? 'Orders appear here once their payments are reconciled on the show\'s Payments tab.' : ''}
        </div>
      ) : (
        <table className="table" data-testid="orders">
          <thead>
            <tr>
              <th>Show</th>
              <th>Buyer</th>
              <th className="num">Items</th>
              <th>Total</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {q.data.orders.map((o) => (
              <tr key={o.id} data-testid={`order-${o.buyer}`}>
                <td>{o.showDate}</td>
                <td>
                  <Link to={`/app/orders/${o.id}`}>
                    <b>{o.buyer}</b>
                  </Link>
                </td>
                <td className="num">{o.items}</td>
                <td>{money(o.totalCents)}</td>
                <td>
                  <span className={`status ${o.status}`}>{o.status}</span>
                  {!o.verified && o.status === 'open' && <span className="muted small"> · unpaid items</span>}
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
  const [error, setError] = useState<string | null>(null)
  const status = useMutation({
    mutationFn: (to: OrderStatus) => api('POST', `/api/orders/${id}/status`, { status: to, tracking: tracking || undefined }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  const save = useMutation({
    mutationFn: () => api('PATCH', `/api/orders/${id}`, { notes, tracking }),
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
        <span className={`status ${o.status}`}>{o.status}</span>
      </div>
      <div className="card slip" data-testid="packing-slip">
        <table className="table">
          <thead>
            <tr>
              <th>Item</th>
              <th>SKU</th>
              <th className="num">Qty</th>
              <th>Price</th>
              <th>Payment</th>
            </tr>
          </thead>
          <tbody>
            {o.sales?.map((s) => (
              <tr key={s.id} className={['cancelled', 'payment_failed'].includes(s.status) ? 'disabled' : ''}>
                <td>{s.sku ? `${s.sku.productName} ${s.sku.name}`.trim() : s.item}</td>
                <td>{s.sku ? <code>{s.sku.code}</code> : <span className="badge warn">unmatched</span>}</td>
                <td className="num">{s.quantity}</td>
                <td>{money(s.priceCents)}</td>
                <td>
                  <span className={`status ${s.status}`}>{s.status.replace('_', ' ')}</span>
                  {s.externalOrderId && <span className="muted small"> · #{s.externalOrderId}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          {o.items} item{o.items === 1 ? '' : 's'} · {money(o.totalCents)} · {o.verified ? 'all paid' : 'not all paid'}
        </p>
        <button className="btn small" onClick={() => window.print()}>
          Print packing slip
        </button>
      </div>
      {can('orders.fulfill') && (
        <div className="card">
          <label className="field">
            <span>Tracking number</span>
            <input value={tracking || o.tracking || ''} onChange={(e) => setTracking(e.target.value)} />
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
