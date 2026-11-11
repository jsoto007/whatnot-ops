import { Fragment, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { api, apiGet, money, type ChatMessage, type ParseStats, type ParsedEvent, type Sale, type SaleChatLine, type SaleStatus, type Show, type ShowTiles, type TopBuyer, type TopSku, type WalkAway, type WhatnotRecap as WhatnotRecapData } from '../../lib/api'
import { fmtLongDate, fmtShopTime } from '../../lib/format'
import { lotLabel, saleStatusLabel } from '../../lib/labels'
import { useDebounced } from '../../lib/useDebounced'
import { Kpis, Tally, TopLists } from './Dashboard'
import { Reconcile } from './Reconcile'
import { WhatnotRecap } from './WhatnotNumbers'
import { WhatnotOrders } from './WhatnotOrders'

const KIND_LABEL: Record<string, string> = { sale: 'Sale', claim: 'Claim', gift: 'Gift', cancel: 'Cancel', payment_failed: 'Payment failed', no_pick: 'No winner', listing: 'Listing', chatter: 'Chatter' }
const TABS = ['sales', 'walkaways', 'payments', 'events', 'messages', 'analytics'] as const
type Tab = (typeof TABS)[number]
const TAB_LABEL: Record<Tab, string> = { sales: 'Sales', walkaways: 'Walked away', payments: 'Payments', events: 'Parsed lines', messages: 'Chat', analytics: 'Analytics' }

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
  const dash = useQuery({ queryKey: ['show-dashboard', id], queryFn: () => apiGet<{ tiles: ShowTiles; topBuyers: TopBuyer[]; topSkus: TopSku[]; winnersWithoutPurchase: WalkAway[]; whatnot?: WhatnotRecapData | null }>(`/api/dashboard/shows/${id}`) })
  const sales = useQuery({ queryKey: ['sales', id], queryFn: () => apiGet<{ sales: Sale[] }>(`/api/shows/${id}/sales`) })
  const parse = useMutation({ mutationFn: () => api<ParseStats>('POST', `/api/shows/${id}/parse`), onSuccess: () => qc.invalidateQueries() })
  const aiAudit = useMutation({ mutationFn: () => api<ParseStats>('POST', `/api/shows/${id}/ai-audit`), onSuccess: () => qc.invalidateQueries() })

  const t = dash.data?.tiles
  const s = show.data?.show
  const list = sales.data?.sales ?? []
  const walkaways = dash.data?.winnersWithoutPurchase ?? []
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
          {s?.title && (
            <div className="sub" data-testid="show-title">
              {s.title}
              {s.recorded === false ? ' · not recorded on a laptop; its sales come from Whatnot’s orders' : ''}
            </div>
          )}
          {s && (
            <div className="sub">
              Started {started!.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · {t?.live ? `${mins} min so far` : `${mins} min of chat`} · {s.messageCount} messages
              {me?.organization.hostUsername ? ` · host ${me.organization.hostUsername}` : ''}
            </div>
          )}
        </div>
        <span className="row-actions">
          {can('orders.view') && (
            <Link className="btn ghost" to={`/app/orders/slips/${id}`} data-testid="order-slips-link">
              Order slips
            </Link>
          )}
          {can('orders.view') && (
            <Link className="btn ghost" to={`/app/orders/shipping/${id}`} data-testid="show-shipping-link">
              Shipping
            </Link>
          )}
          {can('review.act') && (
            <>
              <button className="btn ghost" onClick={() => parse.mutate()} disabled={parse.isPending} data-testid="parse-now">
                {parse.isPending ? 'Parsing…' : 'Parse now'}
              </button>
              {/* 409 while the worker is parsing this show: "try again in a few seconds" */}
              {parse.isError && (
                <span className="form-error small" data-testid="parse-error">
                  {(parse.error as Error).message}
                </span>
              )}
              <button className="btn ghost" onClick={() => aiAudit.mutate()} disabled={aiAudit.isPending} data-testid="ai-audit" title="Ask the AI supervisor to check this show">
                {aiAudit.isPending ? 'Auditing…' : 'Run AI audit'}
              </button>
              {aiAudit.isError && <span className="form-error small">{(aiAudit.error as Error).message}</span>}
            </>
          )}
        </span>
      </div>

      {t && <Kpis t={t} label="This show" />}
      {list.length > 0 && <SaleStatusBar counts={counts} />}
      {t && <Tally t={t} />}
      {t && s && <NextSteps t={t} counts={counts} walkaways={walkaways.length} showId={id} canReconcile={can('orders.reconcile')} canReview={can('review.act')} onTab={setTab} />}

      <div className="tabs" role="tablist">
        {TABS.map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
            {TAB_LABEL[k]}
            {k === 'sales' && list.length ? <span className="tab-count">{list.length}</span> : null}
            {k === 'walkaways' && walkaways.length ? <span className="tab-count">{walkaways.length}</span> : null}
          </button>
        ))}
      </div>
      {tab === 'sales' && <SalesTab sales={list} loading={sales.isPending} />}
      {tab === 'walkaways' && <WalkawaysTab rows={walkaways} loading={dash.isPending} canReview={can('review.act')} showId={id} onTab={setTab} />}
      {tab === 'payments' && (
        <>
          {can('orders.view') && <WhatnotOrders showId={id} canApply={can('orders.reconcile')} />}
          {can('orders.reconcile') ? <Reconcile showId={id} /> : <p className="card muted">Only owners and managers can reconcile payments.</p>}
        </>
      )}
      {tab === 'events' && <EventsTab showId={id} />}
      {tab === 'messages' && <MessagesTab showId={id} />}
      {tab === 'analytics' && dash.data && (
        <>
          {dash.data.whatnot && <WhatnotRecap w={dash.data.whatnot} />}
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
const LABEL: Record<SaleStatus, string> = { fulfilled: 'Shipped', paid: 'Paid', reserved: 'Reserved', detected: 'Needs a decision', payment_failed: 'Payment declined', cancelled: 'Cancelled' }

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

function NextSteps({ t, counts, walkaways, showId, canReconcile, canReview, onTab }: { t: ShowTiles; counts: Counts; walkaways: number; showId: string; canReconcile: boolean; canReview: boolean; onTab: (t: Tab) => void }) {
  const steps: React.ReactNode[] = []
  if (walkaways) steps.push(<li key="w">{walkaways} winner{walkaways === 1 ? '' : 's'} walked away (nothing picked, cancelled, unpaid) — <a href="#walkaways" onClick={(e) => { e.preventDefault(); onTab('walkaways') }}>see who</a></li>)
  if (canReview && t.openReviews) steps.push(<li key="r">{t.openReviews} line{t.openReviews === 1 ? '' : 's'} need{t.openReviews === 1 ? 's' : ''} a decision — <Link to={`/app/review/${showId}`}>open Review</Link></li>)
  // Whatnot charges every win, so sales are paid at once. Only sales recorded before that (still
  // reserved) wait for the payment file.
  const open = counts.reserved?.n ?? 0
  if (canReconcile && open && !t.live) steps.push(<li key="p">{open} older sale{open === 1 ? '' : 's'} still wait{open === 1 ? 's' : ''} for the payment file — <a href="#payments" onClick={(e) => { e.preventDefault(); onTab('payments') }}>reconcile payments</a></li>)
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
  { key: 'paid', label: 'Paid', match: (s) => s.status === 'paid' || s.status === 'fulfilled' },
  { key: 'flagged', label: 'Needs a decision', match: (s) => s.flagged || s.status === 'detected' },
  { key: 'declined', label: 'Payment declined', match: (s) => s.status === 'payment_failed' },
  { key: 'lost', label: 'Cancelled / declined', match: (s) => s.status === 'cancelled' || s.status === 'payment_failed' },
]

/**
 * Sales grouped by buyer, the way the team's sheet reads, with the line number so a row can
 * be found in chat. Each sale can open what its winner said around the win, to hold a
 * "can I get the blue one instead" against the item and price recorded.
 */
function SalesTab({ sales, loading }: { sales: Sale[]; loading: boolean }) {
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<number>>(() => new Set())
  const toggle = (id: number) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })
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
            <th title="Central time">Won</th>
            <th>Item</th>
            <th className="num">Qty</th>
            <th className="num">Total</th>
            <th>Type</th>
            <th>Status</th>
            <th>Chat</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(([buyer, rows]) =>
            rows.map((s, i) => (
              <Fragment key={s.id}>
              <tr className={(s.flagged ? 'low ' : '') + (i === 0 ? 'group-start' : '')} data-testid={`sale-${s.id}`}>
                <td>{i === 0 ? <b>{buyer}</b> : <span className="muted small">↳</span>}</td>
                <td className="num muted">{s.seq ?? ''}</td>
                <td className="muted small nowrap" data-testid={`won-at-${s.id}`}>{s.wonAt ? fmtShopTime(s.wonAt) : '—'}</td>
                <td title={s.rawText ?? ''}>
                  {s.sku ? `${s.sku.productName} ${s.sku.name}`.trim() : s.item}
                  {s.sku ? <code className="sku">{s.sku.code}</code> : <span className="badge warn">unmatched</span>}
                </td>
                <td className="num">{s.quantity}</td>
                <td className="num">{money((s.priceCents ?? 0) * s.quantity)}</td>
                <td className="muted">{lotLabel(s.auctionType)}</td>
                <td>
                  <span className={`status ${s.status}`}>{saleStatusLabel(s.status)}</span>
                  {s.statusReason && <div className="muted small">{s.statusReason}</div>}
                </td>
                <td>
                  {s.chat?.length ? (
                    <button className="btn ghost small" aria-expanded={open.has(s.id)} onClick={() => toggle(s.id)} data-testid={`chat-toggle-${s.id}`} title="What the winner said around this win">
                      {s.chat.length} line{s.chat.length === 1 ? '' : 's'}
                    </button>
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
              </tr>
              {open.has(s.id) && s.chat && (
                <tr className="sale-chat-row">
                  <td colSpan={10}>
                    <SaleChat sale={s} lines={s.chat} />
                  </td>
                </tr>
              )}
              </Fragment>
            )),
          )}
        </tbody>
      </table>
    </>
  )
}

const ROLE_LABEL: Record<SaleChatLine['role'], string> = { buyer: 'winner', host: 'host', platform: 'platform', other: 'mentions' }

/** The winner's lines next to what was recorded: the person decides if the item or price should change. */
function SaleChat({ sale, lines }: { sale: Sale; lines: SaleChatLine[] }) {
  return (
    <div className="sale-chat" data-testid={`sale-chat-${sale.id}`}>
      <p className="muted small">
        Recorded: <b>{sale.sku ? `${sale.sku.productName} ${sale.sku.name}`.trim() : sale.item || 'no item named'}</b> at <b>{money(sale.priceCents)}</b>
        {sale.wonAt ? ` · won ${fmtShopTime(sale.wonAt)}` : ''}. Chat is shown as typed; nothing here changes the sale.
      </p>
      <div className="chat small">
        {lines.map((c) => (
          <div className={'chat-line' + (c.role === 'buyer' ? ' hit' : '')} key={c.seq}>
            <span className="t">{fmtShopTime(c.capturedAt)}</span>
            <span className="u">
              {c.username || 'platform'} <span className="muted small">· {ROLE_LABEL[c.role]}</span>
            </span>
            <span>{c.body}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const WALKAWAY_CLASS: Record<WalkAway['outcome'], string> = { no_item: 'detected', cancelled: 'cancelled', payment_failed: 'payment_failed', unpaid: 'reserved', unconfirmed: 'detected' }

/** Winners with nothing to pack: who won, why nothing is going out, and the chat line to check. */
function WalkawaysTab({ rows, loading, canReview, showId, onTab }: { rows: WalkAway[]; loading: boolean; canReview: boolean; showId: string; onTab: (t: Tab) => void }) {
  if (loading) return <p className="muted">Loading…</p>
  if (!rows.length)
    return (
      <div className="empty" data-testid="walkaways-empty">
        <b>Every winner has something to pack</b>
        Buyers who won and picked nothing, cancelled, failed to pay or are missing from the payment file will show here.
      </div>
    )
  const unconfirmed = rows.filter((r) => r.outcome === 'unconfirmed').length
  return (
    <>
      <p className="muted small">
        One row per win that ended with nothing to pack. "Won, nothing picked" is the sheet's No-Pick; the line number finds the moment in <a href="#messages" onClick={(e) => { e.preventDefault(); onTab('messages') }}>Chat</a>.
        {unconfirmed > 0 && canReview && (
          <>
            {' '}
            {unconfirmed} win{unconfirmed === 1 ? ' is' : 's are'} waiting for a decision in <Link to={`/app/review/${showId}`}>Review</Link>.
          </>
        )}
      </p>
      <table className="table walkaways" data-testid="walkaways">
        <thead>
          <tr>
            <th>Buyer</th>
            <th>Outcome</th>
            <th>Item</th>
            <th className="num">Qty</th>
            <th className="num">#</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.outcome}-${r.saleId ?? r.seq ?? i}`} data-testid={`walkaway-${r.buyer}`}>
              <td>
                <b>{r.buyer}</b>
              </td>
              <td>
                <span className={`status ${WALKAWAY_CLASS[r.outcome]}`}>{r.label}</span>
              </td>
              <td>{r.item || <span className="muted">nothing named</span>}</td>
              <td className="num">{r.quantity}</td>
              <td className="num muted">{r.seq ?? ''}</td>
              <td className="muted small">{r.note}</td>
            </tr>
          ))}
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
              <th>Confidence</th>
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
                <td className="muted num">{Math.round(e.confidence * 100)}%</td>
                <td className="muted">{e.rawText}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

/** How long the search box waits for typing to pause before it asks the server. */
export const CHAT_SEARCH_MS = 300

function MessagesTab({ showId }: { showId: string }) {
  const [q, setQ] = useState('')
  // One request per pause in typing, not per keystroke (each is a search of the whole show and
  // counts against the chat-search rate limit); the last results stay up meanwhile.
  const term = useDebounced(q, CHAT_SEARCH_MS)
  const msgs = useQuery({
    queryKey: ['messages', showId, term],
    queryFn: () => apiGet<{ total: number; messages: ChatMessage[] }>(`/api/shows/${showId}/messages?limit=500&q=${encodeURIComponent(term)}`),
    // Keep the last results while a new search loads, but never another show's lines.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === showId ? keepPreviousData(prev) : undefined),
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
