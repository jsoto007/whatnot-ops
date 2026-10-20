import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { api, apiGet, money, type ChatMessage, type ParseStats, type ParsedEvent, type Sale, type Show } from '../../lib/api'

const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
const KIND_LABEL: Record<string, string> = {
  sale: 'Sale',
  claim: 'Claim',
  gift: 'Gift',
  cancel: 'Cancel',
  payment_failed: 'Payment failed',
  no_pick: 'No pick',
  listing: 'Listing',
  chatter: '',
}

export function ShowDetail() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'sales' | 'events' | 'messages'>('sales')
  const show = useQuery({ queryKey: ['show', id], queryFn: () => apiGet<{ show: Show; stats: ParseStats }>(`/api/shows/${id}`) })
  const parse = useMutation({
    mutationFn: () => api<ParseStats>('POST', `/api/shows/${id}/parse`),
    onSuccess: () => qc.invalidateQueries(),
  })
  return (
    <section>
      <p>
        <Link to="/app/shows">← Shows</Link>
      </p>
      <div className="product-head">
        <h2>{show.data ? fmtDate(show.data.show.date) + ' show' : 'Show'}</h2>
        {can('review.act') && (
          <button className="btn" onClick={() => parse.mutate()} disabled={parse.isPending} data-testid="parse-now">
            {parse.isPending ? 'Parsing…' : 'Parse now'}
          </button>
        )}
      </div>
      {show.data && <Stats stats={show.data.stats} total={show.data.show.messageCount} />}
      <div className="tabs" role="tablist">
        {(['sales', 'events', 'messages'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t[0]!.toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      {tab === 'sales' && <SalesTab showId={id} />}
      {tab === 'events' && <EventsTab showId={id} />}
      {tab === 'messages' && <MessagesTab showId={id} />}
    </section>
  )
}

function Stats({ stats, total }: { stats: ParseStats; total: number }) {
  const s = stats.sales
  const tile = (label: string, value: number | string, testid: string) => (
    <div className="tile" data-testid={testid}>
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
    </div>
  )
  return (
    <div className="tiles">
      {tile('Messages', total, 'tile-messages')}
      {tile('Parsed', stats.parsed, 'tile-parsed')}
      {tile('Sales', (s.reserved ?? 0) + (s.detected ?? 0) + (s.paid ?? 0) + (s.fulfilled ?? 0), 'tile-sales')}
      {tile('Reserved', s.reserved ?? 0, 'tile-reserved')}
      {tile('Cancelled / failed', (s.cancelled ?? 0) + (s.payment_failed ?? 0), 'tile-cancelled')}
      {tile('Gifts', stats.events.gift ?? 0, 'tile-gifts')}
      {tile('No-picks', stats.events.no_pick ?? 0, 'tile-nopicks')}
      {tile('Needs review', stats.openReviews, 'tile-review')}
      {stats.job && stats.job.status !== 'done' && tile('Parse job', stats.job.status, 'tile-job')}
    </div>
  )
}

export function SalesTable({ sales, showShow }: { sales: Sale[]; showShow?: boolean }) {
  return (
    <table className="table" data-testid="sales">
      <thead>
        <tr>
          {showShow && <th>Show</th>}
          <th>#</th>
          <th>Buyer</th>
          <th>Item</th>
          <th>SKU</th>
          <th className="num">Qty</th>
          <th>Price</th>
          <th>Type</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {sales.map((s) => (
          <tr key={s.id} className={s.flagged ? 'low' : ''} data-testid={`sale-${s.id}`}>
            {showShow && <td>{s.showDate}</td>}
            <td className="num">{s.seq ?? ''}</td>
            <td>
              <b>{s.buyer}</b>
            </td>
            <td title={s.rawText ?? ''}>{s.item}</td>
            <td>{s.sku ? <code>{s.sku.code}</code> : <span className="badge warn">unmatched</span>}</td>
            <td className="num">{s.quantity}</td>
            <td>{money(s.priceCents)}</td>
            <td>{s.auctionType}</td>
            <td>
              <span className={`status ${s.status}`}>{s.status.replace('_', ' ')}</span>
              {s.statusReason && <div className="muted small">{s.statusReason}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function SalesTab({ showId }: { showId: string }) {
  const q = useQuery({ queryKey: ['sales', showId], queryFn: () => apiGet<{ sales: Sale[] }>(`/api/shows/${showId}/sales`) })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load sales.</p>
  if (!q.data.sales.length) return <p className="card muted">No sales detected yet. Press "Parse now" after chat has been captured.</p>
  return <SalesTable sales={q.data.sales} />
}

function EventsTab({ showId }: { showId: string }) {
  const [kind, setKind] = useState('')
  const q = useQuery({
    queryKey: ['events', showId, kind],
    queryFn: () => apiGet<{ events: ParsedEvent[]; total: number }>(`/api/shows/${showId}/events?limit=500&kind=${kind}`),
  })
  return (
    <>
      <div className="toolbar">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Event kind">
          <option value="">All but chatter</option>
          {Object.keys(KIND_LABEL).map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k] || 'Chatter'}
            </option>
          ))}
        </select>
        <span className="muted">{q.data ? `${q.data.total} events` : ''}</span>
      </div>
      {q.data && (
        <table className="table small">
          <thead>
            <tr>
              <th>#</th>
              <th>Kind</th>
              <th>Buyer</th>
              <th>Item</th>
              <th className="num">Qty</th>
              <th>Price</th>
              <th>Rule</th>
              <th>Line</th>
            </tr>
          </thead>
          <tbody>
            {q.data.events.map((e) => (
              <tr key={e.id}>
                <td className="num">{e.seq}</td>
                <td>
                  <code>{e.kind}</code>
                </td>
                <td>{e.buyer}</td>
                <td>{e.item}</td>
                <td className="num">{e.quantity}</td>
                <td>{money(e.priceCents)}</td>
                <td className="muted">
                  {e.rule} · {Math.round(e.confidence * 100)}%
                </td>
                <td className="muted">{e.rawText}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

function MessagesTab({ showId }: { showId: string }) {
  const [q, setQ] = useState('')
  const msgs = useQuery({
    queryKey: ['messages', showId, q],
    queryFn: () => apiGet<{ total: number; messages: ChatMessage[] }>(`/api/shows/${showId}/messages?limit=500&q=${encodeURIComponent(q)}`),
  })
  return (
    <>
      <div className="toolbar">
        <input type="search" placeholder="Buyer name or word" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search messages" />
        <span className="muted">{msgs.data ? `${msgs.data.total} message${msgs.data.total === 1 ? '' : 's'}` : ''}</span>
      </div>
      {msgs.data && (
        <div className="chat">
          {msgs.data.messages.map((m) => (
            <div className="chat-line" key={m.id}>
              <span className="t">{new Date(m.capturedAt).toLocaleTimeString()}</span>
              <span className="u">{m.username}</span>
              <span>{m.body}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
