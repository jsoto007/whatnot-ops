import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { Icon, type IconName } from '../../components/Icon'
import { apiGet, money, type Overview, type ShowTiles, type TopBuyer, type TopSku } from '../../lib/api'
import { fmtDate, fmtLongDate } from '../../lib/format'


/** The four numbers an owner asks about first. */
export function Kpis({ t, totals, label }: { t?: ShowTiles; totals?: Record<string, number>; label?: string }) {
  const v = (k: keyof ShowTiles) => (t ? (t[k] as number) : (totals?.[k] ?? 0))
  const buyers = v('buyers')
  const avg = buyers ? Math.round(v('revenuePaidCents') / buyers) : 0
  const tile = (title: string, value: string | number, id: string, note: string, accent?: string) => (
    <div className={'tile' + (accent ? ' accent-' + accent : '')} data-testid={`tile-${id}`}>
      <div className="tile-label">{title}</div>
      <div className="tile-value">{value}</div>
      <div className="muted small">{note}</div>
    </div>
  )
  return (
    <div className="tiles primary" aria-label={label ?? 'Key numbers'}>
      {tile('Paid revenue', money(v('revenuePaidCents')), 'paid', v('revenuePendingCents') ? `${money(v('revenuePendingCents'))} still awaiting payment` : 'nothing awaiting payment', 'green')}
      {tile('Items sold', v('itemsSold'), 'items', `${v('cancelled')} cancelled or failed`)}
      {tile('Buyers', buyers, 'buyers', buyers ? `${money(avg)} paid per buyer` : 'no buyers yet')}
      {tile('Messages', v('messages'), 'messages', `${v('gifts')} gifts · ${v('noPicks')} no-picks`)}
    </div>
  )
}

/** The team's spreadsheet tally for one show, in their words and order. */
export function Tally({ t }: { t: ShowTiles }) {
  const cell = (label: string, value: string | number, id: string) => (
    <div className="tile" data-testid={`tile-${id}`}>
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
    </div>
  )
  return (
    <div className="tally" aria-label="Show tally">
      {cell('Items sold', t.itemsSold, 'tally-items')}
      {cell('Buyers', t.buyers, 'tally-buyers')}
      {cell('Red', t.red, 'red')}
      {cell('Gifts', t.gifts, 'gifts')}
      {cell('No-picks', t.noPicks, 'nopicks')}
      {cell('Bundles', t.bundles, 'bundles')}
      {cell('Ultra / Mega', `${t.ultra} · ${t.mega}`, 'ultramega')}
    </div>
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
          <Bars label="Buyer" value="Items" rows={buyers.map((b) => ({ key: b.buyer, label: `${b.buyer} · ${money(b.paidCents)} paid`, value: b.items }))} />
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

function AttentionItem({ icon, count, text, to, verb, tone }: { icon: IconName; count: number | string; text: string; to: string; verb: string; tone: 'amber' | 'blue' | 'red' | 'grey' }) {
  return (
    <li className={`attn attn-${tone}`}>
      <span className="attn-icon">
        <Icon name={icon} />
      </span>
      <span className="attn-text">
        <b>{count}</b> {text}
      </span>
      <Link to={to} className="btn small">
        {verb}
      </Link>
    </li>
  )
}

export function Dashboard() {
  const { can } = useAuth()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => apiGet<Overview>('/api/dashboard'), refetchInterval: 30_000 })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the dashboard.</p>
  const d = q.data
  const a = d.attention
  const live = a.live
  const items: React.ReactNode[] = []
  if (can('review.act') && a.openReviews) items.push(<AttentionItem key="r" icon="review" count={a.openReviews} text={a.openReviews === 1 ? 'decision waiting in Review' : 'decisions waiting in Review'} to="/app/review" verb="Resolve" tone="amber" />)
  if (can('orders.view') && a.toFulfil) items.push(<AttentionItem key="o" icon="orders" count={a.toFulfil} text={a.toFulfil === 1 ? 'paid order to pick and ship' : 'paid orders to pick and ship'} to="/app/orders?view=queue" verb="Pick" tone="blue" />)
  for (const s of a.unreconciledShows.slice(0, 3)) {
    if (!can('orders.reconcile')) break
    items.push(<AttentionItem key={'u' + s.showId} icon="shows" count={money(s.pendingCents)} text={`awaiting payment from the ${fmtDate(s.date)} show`} to={`/app/shows/${s.showId}?tab=payments`} verb="Reconcile" tone="grey" />)
  }
  if (can('inventory.view') && a.lowStock) items.push(<AttentionItem key="s" icon="inventory" count={a.lowStock} text={a.lowStock === 1 ? 'product at or below its low-stock line' : 'products at or below their low-stock line'} to="/app/inventory" verb="Restock" tone="red" />)

  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Dashboard</h2>
          <div className="sub">
            {d.totals.shows} show{d.totals.shows === 1 ? '' : 's'} captured · updates every 30 s
          </div>
        </div>
      </div>

      {live && (
        <Link to={`/app/shows/${live.showId}`} className="card live-card" data-testid="live-card">
          <span className="live-pill">Live now</span>
          <span className="live-stats">
            <b>{live.messages}</b> messages · <b>{live.itemsSold}</b> items sold to <b>{live.buyers}</b> buyers · {money(live.revenuePendingCents)} in sales so far
            {live.openReviews ? ` · ${live.openReviews} to review` : ''}
          </span>
          <span className="btn small">Open show</span>
        </Link>
      )}

      <div className="two-col dash-top">
        <div>
          <h3 style={{ marginTop: 0 }}>Needs attention</h3>
          {items.length ? (
            <ul className="attention" data-testid="attention">
              {items}
            </ul>
          ) : (
            <div className="empty" data-testid="attention-empty">
              <b>All clear</b>
              Nothing is waiting on a person right now.
            </div>
          )}
        </div>
        <div>
          <h3 style={{ marginTop: 0 }}>All shows</h3>
          <Kpis totals={d.totals} />
        </div>
      </div>

      <h3>Show by show</h3>
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
              <th className="num">Items</th>
              <th className="num">Buyers</th>
              <th className="num">Paid</th>
              <th className="num">Awaiting</th>
              <th className="num">Cancelled</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {d.shows.map((s) => {
              const state = s.live ? 'live' : s.revenuePendingCents > 0 ? 'awaiting payment' : s.toFulfil ? 'to fulfil' : 'done'
              return (
                <tr key={s.showId}>
                  <td>
                    <Link to={`/app/shows/${s.showId}`}>{fmtLongDate(s.date)}</Link>
                  </td>
                  <td className="num">{s.itemsSold}</td>
                  <td className="num">{s.buyers}</td>
                  <td className="num">{money(s.revenuePaidCents)}</td>
                  <td className="num">{s.revenuePendingCents ? money(s.revenuePendingCents) : '—'}</td>
                  <td className="num">{s.cancelled || '—'}</td>
                  <td>
                    <span className={'status ' + (state === 'live' ? 'paid' : state === 'done' ? 'fulfilled' : state === 'to fulfil' ? 'picking' : 'open')}>{state}</span>
                    {s.openReviews ? <span className="badge warn">{s.openReviews} to review</span> : null}
                  </td>
                </tr>
              )
            })}
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
