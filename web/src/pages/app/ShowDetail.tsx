import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { api, apiGet, money, type ChatMessage, type ParseStats, type ParsedEvent, type Sale, type SaleStatus, type Show, type ShowTiles, type TopBuyer, type TopSku } from '../../lib/api'
import { fmtLongDate } from '../../lib/format'
import { Kpis, Tally, TopLists } from './Dashboard'
import { Reconcile } from './Reconcile'

const KIND_LABEL: Record<string, string> = { sale: 'Sale', claim: 'Claim', gift: 'Gift', cancel: 'Cancel', payment_failed: 'Payment failed', no_pick: 'No pick', listing: 'Listing', chatter: 'Chatter' }
const TABS = ['sales', 'payments', 'events', 'messages', 'analytics'] as const
type Tab = (typeof TABS)[number]
const TAB_LABEL: Record<Tab, string> = { sales: 'Sales', payments: 'Payments', events: 'Parsed lines', messages: 'Chat', analytics: 'Analytics' }

export function ShowDetail() {
  const { id = '' } = useParams()
  const { can, me } = useAuth()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const tab = (TABS as readonly string[]).includes(params.get('tab') ?? '') ? (params.get('tab') as Tab) : 'sales'
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params)
    next.set('tab', t)
    setParams(next, { replace: true })
  }
  const show = useQuery({ queryKey: ['show', id], queryFn: () => apiGet<{ show: Show; stats: ParseStats }>(`/api/shows/${id}`) })
  const dash = useQuery({ queryKey: ['show-dashboard', id], queryFn: () => apiGet<{ tiles: ShowTiles; topBuyers: TopBuyer[]; topSkus: TopSku[] }>(`/api/dashboard/shows/${id}`) })
  const sales = useQuery({ queryKey: ['sales', id], queryFn: () => apiGet<{ sales: Sale[] }>(`/api/shows/${id}/sales`) })
  const parse = useMutation({ mutationFn: () => api<ParseStats>('POST', `/api/shows/${id}/parse`), onSuccess: () => qc.invalidateQueries() })
  const aiAudit = useMutation({ mutationFn: () => api<ParseStats>('POST', `/api/shows/${id}/ai-audit`), onSuccess: () => qc.invalidateQueries() })

  const t = dash.data?.tiles
  const s = show.data?.show
  const list = sales.data?.sales ?? []
  const counts = useMemo(() => countBy(list), [list])
  const started = s ? new Date(s.startedAt) : null
  const updated = s ? new Date(s.updatedAt) : null
  const mins = started && updated ? Math.max(0, Math.round((updated.getTime() - started.getTime()) / 60000)) : 0

  return (
    <section>
      <p>
        <Link to="/app/shows">← Shows</Link>
      </p>
      <div className="page-head">
        <div>
          <h2>
            {s ? fmtLongDate(s.date) : 'Show'} {t?.live && <span className="live-pill">Live</span>}
          </h2>
          {s && (
            <div className="sub">
              Started {started!.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · {t?.live ? `${mins} min so far` : `${mins} min of chat`} · {s.messageCount} messages
              {me?.organization.hostUsername ? ` · host ${me.organization.hostUsername}` : ''}
            </div>
          )}
        </div>
        {can('review.act') && (
          <span className="row-actions">
            <button className="btn ghost" onClick={() => parse.mutate()} disabled={parse.isPending} data-testid="parse-now">
              {parse.isPending ? 'Parsing…' : 'Parse now'}
            </button>
            <button className="btn ghost" onClick={() => aiAudit.mutate()} disabled={aiAudit.isPending} data-testid="ai-audit" title="Ask the AI supervisor to check this show">
              {aiAudit.isPending ? 'Auditing…' : 'Run AI audit'}
            </button>
            {aiAudit.isError && <span className="form-error small">{(aiAudit.error as Error).message}</span>}
          </span>
        )}
      </div>

      {t && <Kpis t={t} label="This show" />}
      {list.length > 0 && <SaleStatusBar counts={counts} />}
      {t && <Tally t={t} />}
      {t && s && <NextSteps t={t} counts={counts} showId={id} canReconcile={can('orders.reconcile')} canReview={can('review.act')} onTab={setTab} />}

      <div className="tabs" role="tablist">
        {TABS.map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
            {TAB_LABEL[k]}
            {k === 'sales' && list.length ? <span className="tab-count">{list.length}</span> : null}
          </button>
        ))}
      </div>
      {tab === 'sales' && <SalesTab sales={list} loading={sales.isPending} />}
      {tab === 'payments' && (can('orders.reconcile') ? <Reconcile showId={id} /> : <p className="card muted">Only owners and managers can reconcile payments.</p>)}
      {tab === 'events' && <EventsTab showId={id} />}
      {tab === 'messages' && <MessagesTab showId={id} />}
      {tab === 'analytics' && dash.data && (
        <>
          <TopLists buyers={dash.data.topBuyers} skus={dash.data.topSkus} />
          {can('reports.view') && (
            <p>
              <a className="btn" href={`/api/reports/show/${id}.xlsx`}>
                Download this show as .xlsx
              </a>
            </p>
          )}
        </>
      )}
    </section>
  )
}

type Counts = Record<SaleStatus, { n: number; qty: number; cents: number }>
function countBy(sales: Sale[]): Counts {
  const c = {} as Counts
  for (const s of sales) {
    const e = (c[s.status] ??= { n: 0, qty: 0, cents: 0 })
    e.n += 1
    e.qty += s.quantity
    e.cents += (s.priceCents ?? 0) * s.quantity
  }
  return c
}

const ORDER: SaleStatus[] = ['fulfilled', 'paid', 'reserved', 'detected', 'payment_failed', 'cancelled']
const LABEL: Record<SaleStatus, string> = { fulfilled: 'Shipped', paid: 'Paid', reserved: 'Reserved', detected: 'Needs a decision', payment_failed: 'Payment failed', cancelled: 'Cancelled' }

/** Where every sale of the show stands, as one bar: the reader sees at once how much money is settled. */
function SaleStatusBar({ counts }: { counts: Counts }) {
  const total = ORDER.reduce((n, k) => n + (counts[k]?.n ?? 0), 0) || 1
  return (
    <div className="card statusbar" data-testid="status-bar">
      <div className="statusbar-track" aria-hidden>
        {ORDER.map((k) => (counts[k]?.n ? <span key={k} className={`seg ${k}`} style={{ width: `${(100 * counts[k].n) / total}%` }} title={`${LABEL[k]}: ${counts[k].n}`} /> : null))}
      </div>
      <ul className="statusbar-legend">
        {ORDER.map((k) =>
          counts[k]?.n ? (
            <li key={k}>
              <span className={`dot ${k}`} /> <b>{counts[k].n}</b> {LABEL[k].toLowerCase()} <span className="muted">· {money(counts[k].cents)}</span>
            </li>
          ) : null,
        )}
      </ul>
    </div>
  )
}

function NextSteps({ t, counts, showId, canReconcile, canReview, onTab }: { t: ShowTiles; counts: Counts; showId: string; canReconcile: boolean; canReview: boolean; onTab: (t: Tab) => void }) {
  const steps: React.ReactNode[] = []
  if (canReview && t.openReviews) steps.push(<li key="r">{t.openReviews} line{t.openReviews === 1 ? '' : 's'} need{t.openReviews === 1 ? 's' : ''} a decision — <Link to="/app/review">open Review</Link></li>)
  const open = (counts.reserved?.n ?? 0) + (counts.detected?.n ?? 0)
  if (canReconcile && open && !t.live) steps.push(<li key="p">{open} sale{open === 1 ? '' : 's'} await{open === 1 ? 's' : ''} the payment file — <a href="#payments" onClick={(e) => { e.preventDefault(); onTab('payments') }}>reconcile payments</a></li>)
  if (t.toFulfil) steps.push(<li key="f">{t.toFulfil} paid order{t.toFulfil === 1 ? '' : 's'} ready to pick — <Link to={`/app/orders?view=queue&show=${showId}`}>open the queue</Link></li>)
  if (!steps.length) return null
  return (
    <div className="card next-steps" data-testid="next-steps">
      <b>Next</b>
      <ul>{steps}</ul>
    </div>
  )
}

const FILTERS: { key: string; label: string; match: (s: Sale) => boolean }[] = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'open', label: 'Awaiting payment', match: (s) => s.status === 'reserved' || s.status === 'detected' },
  { key: 'paid', label: 'Paid', match: (s) => s.status === 'paid' || s.status === 'fulfilled' },
  { key: 'flagged', label: 'Needs a decision', match: (s) => s.flagged || s.status === 'detected' },
  { key: 'lost', label: 'Cancelled / failed', match: (s) => s.status === 'cancelled' || s.status === 'payment_failed' },
]

/** Sales grouped by buyer, the way the team's sheet reads, with the line number so a row can be found in chat. */
function SalesTab({ sales, loading }: { sales: Sale[]; loading: boolean }) {
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const f = FILTERS.find((x) => x.key === filter)!
  const visible = sales.filter((s) => f.match(s) && (!q || s.buyer.toLowerCase().includes(q.toLowerCase()) || s.item.toLowerCase().includes(q.toLowerCase())))
  const groups = useMemo(() => {
    const m = new Map<string, Sale[]>()
    for (const s of visible) (m.get(s.buyer) ?? m.set(s.buyer, []).get(s.buyer)!).push(s)
    return [...m.entries()].sort((a, b) => (a[1][0]!.seq ?? 0) - (b[1][0]!.seq ?? 0))
  }, [visible])
  if (loading) return <p className="muted">Loading…</p>
  if (!sales.length)
    return (
      <div className="empty">
        <b>No sales detected yet</b>
        Sales appear as the chat is parsed; press "Parse now" if the show has chat but nothing here.
      </div>
    )
  return (
    <>
      <div className="toolbar">
        <div className="chips filter" role="group" aria-label="Filter sales">
          {FILTERS.map((x) => {
            const n = sales.filter(x.match).length
            return (
              <button key={x.key} className={'chip' + (filter === x.key ? ' on' : '')} onClick={() => setFilter(x.key)} disabled={!n && x.key !== 'all'}>
                {x.label} <span className="chip-n">{n}</span>
              </button>
            )
          })}
        </div>
        <input type="search" placeholder="Buyer or item" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search sales" />
      </div>
      <table className="table sales-by-buyer" data-testid="sales">
        <thead>
          <tr>
            <th>Buyer</th>
            <th>#</th>
            <th>Item</th>
            <th className="num">Qty</th>
            <th className="num">Total</th>
            <th>Type</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(([buyer, rows]) =>
            rows.map((s, i) => (
              <tr key={s.id} className={(s.flagged ? 'low ' : '') + (i === 0 ? 'group-start' : '')} data-testid={`sale-${s.id}`}>
                <td>{i === 0 ? <b>{buyer}</b> : <span className="muted small">↳</span>}</td>
                <td className="num muted">{s.seq ?? ''}</td>
                <td title={s.rawText ?? ''}>
                  {s.sku ? `${s.sku.productName} ${s.sku.name}`.trim() : s.item}
                  {s.sku ? <code className="sku">{s.sku.code}</code> : <span className="badge warn">unmatched</span>}
                </td>
                <td className="num">{s.quantity}</td>
                <td className="num">{money((s.priceCents ?? 0) * s.quantity)}</td>
                <td className="muted">{s.auctionType}</td>
                <td>
                  <span className={`status ${s.status}`}>{s.status.replace('_', ' ')}</span>
                  {s.statusReason && <div className="muted small">{s.statusReason}</div>}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </>
  )
}

function EventsTab({ showId }: { showId: string }) {
  const [kind, setKind] = useState('')
  const q = useQuery({ queryKey: ['events', showId, kind], queryFn: () => apiGet<{ events: ParsedEvent[]; total: number }>(`/api/shows/${showId}/events?limit=500&kind=${kind}`) })
  return (
    <>
      <p className="muted small">Every chat line the rules understood, with the rule that fired. Chatter is hidden unless you pick it.</p>
      <div className="toolbar">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Event kind">
          <option value="">All but chatter</option>
          {Object.keys(KIND_LABEL).map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        <span className="muted">{q.data ? `${q.data.total} lines` : ''}</span>
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
                  <span className={'status ' + (e.kind === 'sale' ? 'paid' : e.kind === 'cancel' || e.kind === 'payment_failed' ? 'cancelled' : '')}>{KIND_LABEL[e.kind]}</span>
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
  const msgs = useQuery({ queryKey: ['messages', showId, q], queryFn: () => apiGet<{ total: number; messages: ChatMessage[] }>(`/api/shows/${showId}/messages?limit=500&q=${encodeURIComponent(q)}`) })
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
              <span className="t">{new Date(m.capturedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>
              <span className="u">{m.username || <span className="muted">platform</span>}</span>
              <span>{m.body}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
