import { useEffect, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { FlagChip } from '../../components/BuyerFlag'
import { ApiError, api, apiGet, money, type BuyerFigures, type BuyerFlag, type BuyerList, type BuyerProfile, type Order } from '../../lib/api'
import { fmtDate, fmtLongDate, plural } from '../../lib/format'
import { buyerFlagLabel, orderStatusLabel, saleStatusLabel } from '../../lib/labels'

const SORTS: { key: string; label: string }[] = [
  { key: 'spent', label: 'Most spent' },
  { key: 'items', label: 'Most items' },
  { key: 'shows', label: 'Most shows' },
  { key: 'recent', label: 'Bought most recently' },
  { key: 'problems', label: 'Most wins that fell through' },
  { key: 'name', label: 'Name' },
]
const FILTERS: { key: string; label: string; count: keyof BuyerList['counts'] }[] = [
  { key: '', label: 'Everyone', count: 'all' },
  { key: 'vip', label: 'VIP', count: 'vip' },
  { key: 'watch', label: 'Watch list', count: 'watch' },
  { key: 'noted', label: 'With a note', count: 'noted' },
]

/** "2 payments failed · 1 cancelled", or nothing. */
function fellThrough(f: Pick<BuyerFigures, 'failed' | 'cancelled'>): string {
  const parts = []
  if (f.failed) parts.push(plural(f.failed, 'payment failed', 'payments failed'))
  if (f.cancelled) parts.push(`${f.cancelled} cancelled`)
  return parts.join(' · ')
}

const buyerPath = (name: string) => `/app/buyers/${encodeURIComponent(name)}`

export function Buyers() {
  const [params, setParams] = useSearchParams()
  const flag = params.get('flag') ?? ''
  const sort = params.get('sort') ?? 'spent'
  const offset = Number(params.get('offset') ?? 0) || 0
  const q = params.get('q') ?? ''
  // The box is typed into freely; the list is asked for once the typing pauses.
  const [typed, setTyped] = useState(q)
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    if (!('offset' in changes)) next.delete('offset')
    setParams(next, { replace: true })
  }
  useEffect(() => {
    if (typed === q) return
    const t = setTimeout(() => set({ q: typed.trim() }), 300)
    return () => clearTimeout(t)
    // Only the typing starts the timer; `set` and `q` are read when it fires.
  }, [typed])

  const query = new URLSearchParams({ sort, ...(q ? { q } : {}), ...(flag ? { flag } : {}), ...(offset ? { offset: String(offset) } : {}) })
  const list = useQuery({
    queryKey: ['buyers', query.toString()],
    queryFn: () => apiGet<BuyerList>(`/api/buyers?${query}`),
    placeholderData: keepPreviousData,
  })
  const d = list.data
  const last = d ? Math.min(d.offset + d.buyers.length, d.total) : 0

  return (
    <section className="buyers">
      <div className="page-head">
        <div>
          <h2>Buyers</h2>
          <div className="sub">Everyone who has won in your shows: what they bought, what fell through, and what the team says about them.</div>
        </div>
      </div>
      <div className="toolbar">
        <div className="chips filter" role="group" aria-label="Filter buyers">
          {FILTERS.map((f) => (
            <button key={f.key} className={'chip' + (flag === f.key ? ' on' : '')} onClick={() => set({ flag: f.key })} aria-pressed={flag === f.key}>
              {f.label} {d && <span className="chip-n">{d.counts[f.count]}</span>}
            </button>
          ))}
        </div>
        <input type="search" placeholder="Find a buyer" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Find a buyer" />
        <select value={sort} onChange={(e) => set({ sort: e.target.value })} aria-label="Order">
          {SORTS.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      {list.isError && <p className="form-error">Could not load the buyers.</p>}
      {d && d.buyers.length === 0 && (
        <div className="empty">
          <b>{q || flag ? 'Nobody matches' : 'No buyers yet'}</b>
          {q && !flag ? (
            <>
              Nobody has won under that name.{' '}
              <Link to={buyerPath(q)}>Flag {q} anyway</Link>
            </>
          ) : q || flag ? (
            'Try another filter.'
          ) : (
            'They appear here with their first win.'
          )}
        </div>
      )}
      {d && d.buyers.length > 0 && (
        <>
          <table className="table" data-testid="buyers">
            <thead>
              <tr>
                <th>Buyer</th>
                <th className="num">Shows</th>
                <th className="num">Items</th>
                <th className="num">Spent</th>
                <th>Last bought</th>
                <th>Fell through</th>
              </tr>
            </thead>
            <tbody>
              {d.buyers.map((b) => (
                <tr key={b.username.toLowerCase()} data-testid={`buyer-${b.username}`}>
                  <td className="c-buyer">
                    <Link to={buyerPath(b.username)} className="buyer">
                      {b.username}
                    </Link>
                    <FlagChip flag={b.flag} />
                    {b.hasNote && <span className="buyer-flag noted">Note</span>}
                    {b.names.length > 0 && <span className="buyer-aka">also {b.names.join(', ')}</span>}
                  </td>
                  <td className="num">{b.shows}</td>
                  <td className="num">{b.items}</td>
                  <td className="num">{money(b.paidCents)}</td>
                  <td>{b.lastShow ? fmtLongDate(b.lastShow) : <span className="muted">—</span>}</td>
                  <td>{fellThrough(b) || <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.total > d.buyers.length && (
            <div className="pager">
              <span>
                {d.offset + 1}–{last} of {d.total}
              </span>
              <button className="btn ghost small" disabled={d.offset === 0} onClick={() => set({ offset: String(Math.max(0, d.offset - d.limit)) })}>
                Previous
              </button>
              <button className="btn ghost small" disabled={last >= d.total} onClick={() => set({ offset: String(d.offset + d.limit) })}>
                Next
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

export function BuyerPage() {
  const { name = '' } = useParams()
  const { can } = useAuth()
  const key = ['buyer-page', name.toLowerCase()]
  const q = useQuery({ queryKey: key, queryFn: () => apiGet<{ buyer: BuyerProfile; orders: Order[] }>(`/api/buyers/by-name/${encodeURIComponent(name)}`) })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load this buyer.</p>
  const { buyer: b, orders } = q.data
  const lost = fellThrough(b)
  return (
    <section className="buyer-page">
      <p>
        <Link to="/app/buyers">← Buyers</Link>
      </p>
      <div className="product-head">
        <h2>
          {b.username} <FlagChip flag={b.flag} />
        </h2>
      </div>
      {b.names.length > 0 && <p className="muted">Has also bought as {b.names.join(', ')}.</p>}
      {!b.known && <p className="muted">Nobody has won under this name yet. You can still flag them or write a note, and it will be here when they do.</p>}
      {b.known && (
        <div className="tiles" data-testid="buyer-figures">
          <div className="tile accent-green">
            <div className="tile-value">{money(b.paidCents)}</div>
            <div className="tile-label">Spent</div>
          </div>
          <div className="tile">
            <div className="tile-value">{b.items}</div>
            <div className="tile-label">{b.items === 1 ? 'Item' : 'Items'}</div>
          </div>
          <div className="tile">
            <div className="tile-value">{b.shows}</div>
            <div className="tile-label">
              {b.shows === 1 ? 'Show' : 'Shows'}
              {b.firstShow && b.lastShow && (b.firstShow === b.lastShow ? ` · ${fmtDate(b.firstShow)}` : ` · ${fmtDate(b.firstShow)} to ${fmtDate(b.lastShow)}`)}
            </div>
          </div>
          <div className="tile">
            <div className="tile-value">{b.gifts}</div>
            <div className="tile-label">{b.gifts === 1 ? 'Giveaway won' : 'Giveaways won'}</div>
          </div>
          <div className={'tile' + (lost ? ' accent-red' : '')}>
            <div className="tile-value">{b.failed + b.cancelled}</div>
            <div className="tile-label">{lost || 'Nothing fell through'}</div>
          </div>
        </div>
      )}
      <TeamSays key={name.toLowerCase()} b={b} name={name} canEdit={can('buyers.manage')} queryKey={key} />
      {orders.length > 0 && (
        <>
          <h3>Orders</h3>
          <table className="table small" data-testid="buyer-orders">
            <thead>
              <tr>
                <th>Show</th>
                <th className="num">Items</th>
                <th className="num">Total</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>
                    <Link to={`/app/orders/${o.id}`}>{fmtLongDate(o.showDate)}</Link>
                    {o.buyer.toLowerCase() !== b.username.toLowerCase() && <span className="muted small"> as {o.buyer}</span>}
                  </td>
                  <td className="num">{o.items}</td>
                  <td className="num">{money(o.totalCents)}</td>
                  <td>
                    <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {b.problems.length > 0 && (
        <>
          <h3>Wins that fell through</h3>
          <table className="table small" data-testid="buyer-problems">
            <thead>
              <tr>
                <th>Show</th>
                <th>Lot</th>
                <th className="num">Price</th>
                <th>What happened</th>
              </tr>
            </thead>
            <tbody>
              {b.problems.map((p) => (
                <tr key={p.saleId}>
                  <td>
                    <Link to={`/app/shows/${p.showId}`}>{fmtLongDate(p.showDate)}</Link>
                  </td>
                  <td>
                    {p.quantity > 1 ? `${p.quantity} × ` : ''}
                    {p.item || <span className="muted">not named</span>}
                  </td>
                  <td className="num">{money(p.priceCents)}</td>
                  <td>
                    <span className={`status ${p.status}`}>{saleStatusLabel(p.status)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}

/** The team's flag and note. Everyone reads them; owners, managers and the packing table write. */
function TeamSays({ b, name, canEdit, queryKey }: { b: BuyerProfile; name: string; canEdit: boolean; queryKey: string[] }) {
  const qc = useQueryClient()
  const [flag, setFlag] = useState<BuyerFlag | ''>(b.flag ?? '')
  const [note, setNote] = useState(b.note ?? '')
  const dirty = flag !== (b.flag ?? '') || note.trim() !== (b.note ?? '')
  const save = useMutation({
    mutationFn: () => api<{ buyer: BuyerProfile }>('PATCH', `/api/buyers/by-name/${encodeURIComponent(name)}`, { flag: flag || null, note }),
    onSuccess: (r) => {
      setFlag(r.buyer.flag ?? '')
      setNote(r.buyer.note ?? '')
      qc.setQueryData(queryKey, (old: { buyer: BuyerProfile; orders: Order[] } | undefined) => (old ? { ...old, buyer: r.buyer } : old))
      void qc.invalidateQueries({ queryKey: ['buyer-flags'] })
      void qc.invalidateQueries({ queryKey: ['buyers'] })
    },
  })
  const by = b.notedAt ? `Note last changed${b.notedBy ? ` by ${b.notedBy}` : ''} on ${new Date(b.notedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}.` : ''
  if (!canEdit) {
    if (!b.flag && !b.note) return null
    return (
      <div className="card" data-testid="team-says">
        <h3>What the team says</h3>
        {b.flag && <p>{buyerFlagLabel(b.flag)}</p>}
        {b.note && <p className="buyer-note">{b.note}</p>}
        {by && <p className="muted small">{by}</p>}
      </div>
    )
  }
  return (
    <form
      className="card"
      data-testid="team-says"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <h3>What the team says</h3>
      <label className="field">
        Flag
        <select value={flag} onChange={(e) => setFlag(e.target.value as BuyerFlag | '')}>
          <option value="">No flag</option>
          <option value="vip">VIP: look after them</option>
          <option value="watch">Watch list: check before you ship</option>
        </select>
      </label>
      <label className="field">
        Note
        <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="Anything the next person should know about this buyer" />
      </label>
      <p className="muted small">
        The flag shows beside this buyer's name on Orders and Shipping, and on the show laptop when they win. The note stays on this page. {by}
      </p>
      {save.isError && <p className="form-error">{save.error instanceof ApiError ? save.error.message : 'Could not save. Try again.'}</p>}
      <button className="btn primary" disabled={!dirty || save.isPending}>
        {save.isPending ? 'Saving…' : 'Save'}
      </button>
      {save.isSuccess && !dirty && (
        <span className="muted small" role="status">
          {' '}
          Saved.
        </span>
      )}
    </form>
  )
}
