import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { apiGet, money, type Overview, type ShowTiles, type TopBuyer, type TopSku } from '../../lib/api'

const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

export function Tiles({ t, totals }: { t?: ShowTiles; totals?: Record<string, number> }) {
  const v = (k: keyof ShowTiles) => (t ? (t[k] as number) : (totals?.[k] ?? 0))
  const tile = (label: string, value: number | string, id: string, opts: { note?: string; accent?: string } = {}) => (
    <div className={'tile' + (opts.accent ? ' accent-' + opts.accent : '')} data-testid={`tile-${id}`}>
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
      {opts.note && <div className="muted small">{opts.note}</div>}
    </div>
  )
  const review = v('openReviews')
  const fulfil = v('toFulfil')
  return (
    <>
      <div className="tiles primary">
        {tile('Items sold', v('itemsSold'), 'items')}
        {tile('Buyers', v('buyers'), 'buyers')}
        {tile('Paid revenue', money(v('revenuePaidCents')), 'paid', {
          accent: 'green',
          note: v('revenuePendingCents') ? `${money(v('revenuePendingCents'))} awaiting payment` : 'nothing awaiting payment',
        })}
        {tile('To fulfil', fulfil, 'tofulfil', { accent: fulfil ? 'blue' : undefined, note: fulfil ? 'paid orders not shipped' : 'queue is clear' })}
        {tile('Needs review', review, 'review', { accent: review ? 'amber' : undefined, note: review ? 'decisions waiting' : 'nothing waiting' })}
      </div>
      <div className="tally" aria-label="Show tally">
        {tile('Red', v('red'), 'red')}
        {tile('Bundles', v('bundles'), 'bundles')}
        {tile('Ultra / Mega', `${v('ultra')} · ${v('mega')}`, 'ultramega', { note: `U ${v('ultra')} · M ${v('mega')}` })}
        {tile('Gifts', v('gifts'), 'gifts')}
        {tile('No-picks', v('noPicks'), 'nopicks')}
        {tile('Cancelled / failed', v('cancelled'), 'cancelled', { accent: v('cancelled') ? 'red' : undefined })}
      </div>
    </>
  )
}

export function Bars({ rows, label, value, max, format }: { rows: { key: string; label: string; value: number }[]; label: string; value: string; max?: number; format?: (n: number) => string }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value))
  return (
    <table className="table bars">
      <thead>
        <tr>
          <th>{label}</th>
          <th>{value}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} title={`${r.label}: ${format ? format(r.value) : r.value}`}>
            <td>{r.label}</td>
            <td>
              <div className="bar-row">
                <div className="bar" style={{ width: `${Math.round((100 * r.value) / top)}%` }} aria-hidden />
                <span className="num">{format ? format(r.value) : r.value}</span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function TopLists({ buyers, skus }: { buyers: TopBuyer[]; skus: TopSku[] }) {
  return (
    <div className="two-col">
      <div>
        <h3>Top buyers</h3>
        {buyers.length ? (
          <Bars label="Buyer" value="Items" rows={buyers.map((b) => ({ key: b.buyer, label: `${b.buyer} · ${money(b.paidCents)}`, value: b.items }))} />
        ) : (
          <p className="muted small">No sales yet.</p>
        )}
      </div>
      <div>
        <h3>Top products</h3>
        {skus.length ? (
          <Bars
            label="Product"
            value="Sold"
            rows={skus.map((s) => ({ key: s.code, label: `${s.product} ${s.variant}`.trim() + (s.sellThrough != null ? ` · ${Math.round(s.sellThrough * 100)}% sold through` : ''), value: s.sold }))}
          />
        ) : (
          <p className="muted small">No sales yet.</p>
        )}
      </div>
    </div>
  )
}

export function Dashboard() {
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => apiGet<Overview>('/api/dashboard'), refetchInterval: 30_000 })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the dashboard.</p>
  const d = q.data
  const live = d.shows.find((s) => s.live)
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Dashboard</h2>
          <div className="sub">All shows · {d.totals.shows} show{d.totals.shows === 1 ? '' : 's'}</div>
        </div>
        {live && (
          <Link to={`/app/shows/${live.showId}`} className="live-pill" data-testid="live-pill">
            Live: {fmtDate(live.date)} show, {live.messages} messages
          </Link>
        )}
      </div>
      <Tiles totals={d.totals} />
      {d.lowStock.length > 0 && (
        <p className="card" data-testid="low-stock">
          <b>Low stock:</b> {d.lowStock.map((s) => `${s.product} (${s.available})`).join(', ')}
        </p>
      )}
      <h3>Shows</h3>
      {d.shows.length === 0 ? (
        <div className="empty">
          <b>No shows yet</b>
          Start the Chat Scraper extension on a live show and it will appear here within seconds.
        </div>
      ) : (
        <table className="table" data-testid="shows-table">
          <thead>
            <tr>
              <th>Show</th>
              <th className="num">Messages</th>
              <th className="num">Items</th>
              <th className="num">Buyers</th>
              <th className="num">Paid</th>
              <th className="num">Pending</th>
              <th className="num">Cancelled</th>
              <th className="num">To fulfil</th>
              <th className="num">Review</th>
            </tr>
          </thead>
          <tbody>
            {d.shows.map((s) => (
              <tr key={s.showId}>
                <td>
                  <Link to={`/app/shows/${s.showId}`}>{fmtDate(s.date)}</Link>
                  {s.live && <span className="badge live"> live</span>}
                </td>
                <td className="num">{s.messages}</td>
                <td className="num">{s.itemsSold}</td>
                <td className="num">{s.buyers}</td>
                <td className="num">{money(s.revenuePaidCents)}</td>
                <td className="num">{money(s.revenuePendingCents)}</td>
                <td className="num">{s.cancelled}</td>
                <td className="num">{s.toFulfil}</td>
                <td className="num">{s.openReviews}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {d.shows.length > 1 && (
        <>
          <h3>Items sold per show</h3>
          <Bars label="Show" value="Items" rows={[...d.shows].reverse().map((s) => ({ key: String(s.showId), label: fmtDate(s.date), value: s.itemsSold }))} />
        </>
      )}
      <TopLists buyers={d.topBuyers} skus={d.topSkus} />
    </section>
  )
}
