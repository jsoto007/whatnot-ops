import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { BuyerFlagChip } from '../../components/BuyerFlag'
import { Icon, type IconName } from '../../components/Icon'
import { LiveGaps } from '../../components/LiveGaps'
import { apiGet, money, type Attention, type Overview, type ShowTiles, type TopBuyer, type TopSku, type WhatnotMoney, type WhatnotMoneyData, type WhatnotShowMoney, type WhatnotTopBuyer } from '../../lib/api'
import { fmtDate, fmtWhen, SHOP_TZ } from '../../lib/format'
import { salesLineUpWords } from '../../lib/labels'

const shortMoney = (cents: number) => (cents % 100 === 0 ? `$${(cents / 100).toLocaleString()}` : money(cents))
const weekdayDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const weekday = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'long' })
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

function duration(startIso: string, endIso: string): string {
  const mins = Math.max(0, Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60000))
  return mins < 90 ? `${mins} min` : `${Math.round(mins / 60)} h`
}

/** The four numbers an owner asks about first (used on a show page). */
export function Kpis({ t, totals, label }: { t?: ShowTiles; totals?: Record<string, number>; label?: string }) {
  const v = (k: keyof ShowTiles) => (t ? ((t[k] as number | undefined) ?? 0) : (totals?.[k] ?? 0))
  const buyers = v('buyers')
  // "Paid revenue" is what is paid here: the same figure as the Revenue sheet and the Buyers
  // page. Wins Whatnot says are paid whose product is still to pick (the winner's pick) are
  // shown apart, in the note (dashboard.py adds nothing to "paid" for them).
  const toPick = v('revenueToPickCents')
  const paid = v('revenuePaidCents')
  const avg = buyers ? Math.round(paid / buyers) : 0
  const paidNote = toPick
    ? `${money(toPick)} more paid on Whatnot, ${v('toPick') === 1 ? 'product' : 'products'} to pick`
    : v('revenuePendingCents')
      ? `${money(v('revenuePendingCents'))} in older sales awaiting the payment file`
      : 'charged at the win'

  const tile = (title: string, value: string | number, id: string, note: string) => (
    <div className="tile" data-testid={`tile-${id}`}>
      <div className="tile-label">{title}</div>
      <div className="tile-value">{value}</div>
      <div className="muted small">{note}</div>
    </div>
  )
  return (
    <div className="tiles primary" aria-label={label ?? 'Key numbers'}>
      {tile('Paid revenue', money(paid), 'paid', paidNote)}
      {tile('Items sold', v('itemsSold'), 'items', `${v('cancelled')} cancelled or failed`)}
      {tile('Buyers', buyers, 'buyers', buyers ? `${money(avg)} paid per buyer` : 'no buyers yet')}
      {tile('Messages', v('messages'), 'messages', `${v('gifts')} gifts · ${v('noPicks')} no-picks · ${v('noWinner')} no winner`)}
    </div>
  )
}

/** The team's spreadsheet tally for one show, in their words and order. */
export function Tally({ t }: { t: ShowTiles }) {
  const cell = (label: string, value: string | number, id: string, title?: string) => (
    <div className="tile" data-testid={`tile-${id}`} title={title}>
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
      {cell('No-picks', t.noPicks, 'nopicks', 'Buyers who won and picked nothing (see the Walked away tab)')}
      {cell('Bundles', t.bundles, 'bundles')}
      {cell('Ultra / Mega', `${t.ultra} · ${t.mega}`, 'ultramega')}
      {cell('Quick Hit', t.quickHit, 'quickhit')}
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

function initials(name: string): string {
  const parts = name.replace(/[^A-Za-z0-9]+/g, ' ').trim().split(' ')
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? parts[0]?.[1] ?? '')).toUpperCase()
}

function TopBuyers({ buyers }: { buyers: TopBuyer[] }) {
  const top = Math.max(1, ...buyers.map((b) => b.items))
  return (
    <section className="dcard list-card" aria-labelledby="buyers-title" data-testid="top-buyers">
      <div className="dcard-head">
        <h2 id="buyers-title">Top buyers</h2>
        <span className="muted small">by items</span>
      </div>
      {buyers.length ? (
        <ol className="rank">
          {buyers.slice(0, 6).map((b) => (
            <li key={b.buyer}>
              <span className="avatar-chip" aria-hidden>
                {initials(b.buyer)}
              </span>
              <div className="rank-main">
                <div className="rank-name">{b.buyer}</div>
                <div className="meter" aria-hidden>
                  <span style={{ width: `${Math.round((100 * b.items) / top)}%` }} />
                </div>
              </div>
              <div className="rank-num">
                <div>{plural(b.items, 'item', 'items')}</div>
                <div className={b.paidCents ? 'muted small' : 'small awaiting-ink'}>{b.paidCents ? `${money(b.paidCents)} paid` : 'not confirmed'}</div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No sales yet.</p>
      )}
    </section>
  )
}

function Products({ skus }: { skus: TopSku[] }) {
  return (
    <section className="dcard list-card" aria-labelledby="products-title" data-testid="top-skus">
      <div className="dcard-head">
        <h2 id="products-title">Products</h2>
        <span className="muted small">sold · sell-through</span>
      </div>
      {skus.length ? (
        <ul className="rank">
          {skus.slice(0, 6).map((s) => {
            const pct = s.sellThrough != null ? Math.round(s.sellThrough * 100) : 0
            const scarce = s.available <= 1
            return (
              <li key={s.code}>
                <div className="rank-main">
                  <div className="rank-name">
                    {`${s.product} ${s.variant}`.trim()} <span className="muted small">{s.code}</span>
                  </div>
                  <div className={scarce ? 'small danger-ink' : 'muted small'}>
                    {s.sold} sold · {s.short ? `oversold, buy ${s.short}` : `${s.available} left`}
                  </div>
                </div>
                <div className="sell-through">
                  <div className="meter" role="img" aria-label={`${pct}% sold through`}>
                    <span className={scarce ? 'warn' : 'ok'} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="num">{pct}%</span>
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="muted small">No sales yet.</p>
      )}
    </section>
  )
}

/** Top buyers as Whatnot has them: by what they paid in the period, one row per account. */
function WhatnotTopBuyers({ buyers, days }: { buyers: WhatnotTopBuyer[]; days: number }) {
  const top = Math.max(1, ...buyers.map((b) => b.paidCents))
  return (
    <section className="dcard list-card" aria-labelledby="buyers-title" data-testid="top-buyers">
      <div className="dcard-head">
        <h2 id="buyers-title">Top buyers</h2>
        <span className="muted small">by spend · last {days} days</span>
      </div>
      {buyers.length ? (
        <ol className="rank">
          {buyers.slice(0, 6).map((b, i) => (
            <li key={`${i}-${b.buyer}`}>
              <span className="avatar-chip" aria-hidden>
                {initials(b.buyer)}
              </span>
              <div className="rank-main">
                <div className="rank-name">
                  {b.buyer ? (
                    <>
                      <Link to={`/app/buyers/${encodeURIComponent(b.buyer)}`} className="show-link">
                        {b.buyer}
                      </Link>{' '}
                      <BuyerFlagChip name={b.buyer} />
                    </>
                  ) : (
                    <span className="muted">Name not given by Whatnot</span>
                  )}
                </div>
                <div className="meter" aria-hidden>
                  <span style={{ width: `${Math.round((100 * b.paidCents) / top)}%` }} />
                </div>
              </div>
              <div className="rank-num">
                <div>{money(b.paidCents)}</div>
                <div className="muted small">
                  {plural(b.orders, 'order', 'orders')} · {plural(b.shows, 'show', 'shows')} · {b.returning ? 'returning' : 'new customer'}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No paid orders on Whatnot in the last {days} days.</p>
      )}
    </section>
  )
}

const PERIODS = [7, 30, 90] as const

function PeriodPicker({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="period" role="group" aria-label="Period">
      {PERIODS.map((d) => (
        <button key={d} type="button" className={'chip-btn' + (days === d ? ' on' : '')} aria-pressed={days === d} onClick={() => onChange(d)}>
          {d} days
        </button>
      ))}
    </div>
  )
}

/** Where Whatnot's ledger stands, in words, when it has no net to show (or how fresh it is). */
function ledgerNote(m: WhatnotMoneyData): string {
  if (m.ledgerStatus.notAllowed) return 'The Whatnot connection may not read reports'
  // The ledger is dated by its own lines (a refund lands when it is made), not by order.
  if (m.ledger) return `Whatnot’s ledger lines of these ${m.days} days${m.ledger.through ? `, through ${new Date(m.ledger.through).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: SHOP_TZ })}` : ''}`
  if (m.ledgerStatus.building) return 'Whatnot is still building the report'
  return 'Not read from Whatnot yet'
}

/** The dashboard's money as Whatnot states it: paid sales by new and returning customers, net, fees, buyers. */
function WhatnotMoneyCard({ m, chosen, onDays, stale }: { m: WhatnotMoneyData; chosen: number; onDays: (d: number) => void; stale: boolean }) {
  // Labelled with the period of the numbers on screen (`m.days`): while another period
  // loads, the card is dimmed and still says which days it shows.
  const s = m.sales
  const days = m.days
  const c = m.customers
  const split = c.new.paidCents + c.returning.paidCents
  const lineUp = salesLineUpWords(s)
  const pct = (x: number) => `${split ? (100 * x) / split : 0}%`
  const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: SHOP_TZ })
  const since = c.knownSince ? day(c.knownSince) : null
  // Null: the older orders are still being read. Missing (an older server) says nothing.
  const catchingUp = m.ordersCompleteSince === null
  const completeSince = m.ordersCompleteSince ? day(m.ordersCompleteSince) : null
  const completeUntil = m.ordersCompleteUntil ? day(m.ordersCompleteUntil) : null
  return (
    <section className={'dcard money' + (stale ? ' stale' : '')} aria-labelledby="money-title" aria-busy={stale} data-testid="whatnot-money">
      <div data-testid="tile-paid">
        <div className="money-head">
          <h2 id="money-title" className="eyebrow">
            Sales on Whatnot · last {days} days
          </h2>
          <PeriodPicker days={chosen} onChange={onDays} />
        </div>
        <div className="money-big">{money(s.paidCents)}</div>
        <div className="muted small">{s.orders ? `${plural(s.orders, 'paid order', 'paid orders')} · ${money(s.averageOrderCents)} per order` : 'no paid orders yet'}</div>
        {lineUp && (
          <div className="muted small" data-testid="sales-line-up">
            {lineUp}
          </div>
        )}
        {catchingUp && (
          <div className="muted small" data-testid="orders-catching-up">
            Still reading Whatnot’s older orders, so the earlier days of these {days} may be short.
          </div>
        )}
      </div>
      {split > 0 && (
        <div data-testid="customers">
          <div className="comp-bar" role="img" aria-label={`Sales: ${money(c.new.paidCents)} from new customers, ${money(c.returning.paidCents)} from returning customers`}>
            {c.new.paidCents > 0 && <span className="new-customers" style={{ width: pct(c.new.paidCents) }} />}
            {c.returning.paidCents > 0 && <span className="returning-customers" style={{ width: pct(c.returning.paidCents) }} />}
          </div>
          <div className="legend">
            <span>
              <i className="sw new-customers" aria-hidden /> New customers <b>{money(c.new.paidCents)}</b> <span className="muted">· {plural(c.new.buyers, 'buyer', 'buyers')}</span>
            </span>
            <span>
              <i className="sw returning-customers" aria-hidden /> Returning <b>{money(c.returning.paidCents)}</b> <span className="muted">· {plural(c.returning.buyers, 'buyer', 'buyers')}</span>
            </span>
          </div>
          <div className="muted small customers-note">
            Returning: bought here before these {days} days{since ? `, as far back as Whatnot’s orders in the app go (${since})` : ''}.
            {s.pendingCents > 0 ? ` ${money(s.pendingCents)} more is still being paid.` : ''}
          </div>
        </div>
      )}
      <div className="stat-grid">
        <div data-testid="tile-net">
          <span>Net payout</span>
          <b>{m.ledger ? money(m.ledger.netCents) : '—'}</b>
          <span>{ledgerNote(m)}</span>
        </div>
        <div data-testid="tile-fees">
          <span>Whatnot fees</span>
          <b>{m.feesCents != null ? money(m.feesCents) : '—'}</b>
          <span>commission and payment processing</span>
        </div>
        <div data-testid="tile-buyers">
          <span>Buyers</span>
          <b>{s.buyers}</b>
          <span>
            {c.new.buyers} new · {c.returning.buyers} returning
          </span>
        </div>
        <div data-testid="tile-items">
          <span>Items sold</span>
          <b>{s.items}</b>
          <span>{plural(s.giveaways, 'giveaway', 'giveaways')}</span>
        </div>
      </div>
      <div className="money-foot muted small">
        <span>
          {m.ordersReadAt ? `Orders read from Whatnot ${fmtWhen(m.ordersReadAt)}` : 'Orders not read from Whatnot yet'}
          {completeSince ? ` · all orders read from ${completeSince}${completeUntil ? ` to ${completeUntil}` : ''}` : ''}
          {s.otherCurrencyOrders ? ` · ${plural(s.otherCurrencyOrders, 'order', 'orders')} in another currency not added in` : ''}
        </span>
        <Link to="/app/reports">Fees and payouts →</Link>
      </div>
    </section>
  )
}

export function TopLists({ buyers, skus }: { buyers: TopBuyer[]; skus: TopSku[] }) {
  return (
    <div className="dash-row">
      <TopBuyers buyers={buyers} />
      <Products skus={skus} />
    </div>
  )
}

const REASON_WORDS: Record<string, [string, string]> = {
  unmatched_item: ['unknown item', 'unknown items'],
  insufficient_stock: ['stock conflict', 'stock conflicts'],
  ambiguous_sale: ['unclear cancellation', 'unclear cancellations'],
  low_confidence: ['unconfirmed sale line', 'unconfirmed sale lines'],
  no_open_sale: ['cancel with no sale', 'cancels with no sale'],
  no_payment_record: ['sale missing from a payment file', 'sales missing from a payment file'],
  payment_declined: ['declined payment', 'declined payments'],
  decline_untied: ['decline to match to a sale', 'declines to match to a sale'],
  decline_seen: ['declined card waiting for Whatnot', 'declined cards waiting for Whatnot'],
  lot_won_twice: ['lot with two winners', 'lots with two winners'],
  order_declined: ['declined payment with no sale', 'declined payments with no sale'],
  closed_after_picking: ['item to take out of a package', 'items to take out of a package'],
  swapped_after_picking: ['item to swap in a package', 'items to swap in a package'],
  orders_left_out: ['show with orders left out', 'shows with orders left out'],
  won_after_shipping: ['win after its order shipped', 'wins after their order shipped'],
  unmatched_payment: ['payment with no sale', 'payments with no sale'],
  cancel_sale: ['possible cancellation', 'possible cancellations'],
  payment_failed: ['payment problem', 'payment problems'],
  missing_sale: ['possible missed sale', 'possible missed sales'],
  duplicate_sale: ['possible duplicate', 'possible duplicates'],
  possible_duplicate: ['win seen twice', 'wins seen twice'],
  player_unreadable: ['unreadable win', 'unreadable wins'],
  price_mismatch: ['price to check', 'prices to check'],
}

function joinDates(dates: string[]): string {
  const d = dates.map(fmtDate)
  return d.length <= 1 ? (d[0] ?? '') : `${d.slice(0, -1).join(', ')} and ${d[d.length - 1]}`
}

interface Row {
  key: string
  icon: IconName
  tone: 'amber' | 'blue' | 'grey' | 'red'
  title: string
  sub: string
  to: string
  verb: string
  primary?: boolean
}

function attentionRows(a: Attention, can: (p: string) => boolean, lowStock: Overview['lowStock']): Row[] {
  const rows: Row[] = []
  if (can('review.act') && a.openReviews) {
    const parts = Object.entries(a.reviewReasons)
      .sort((x, y) => y[1] - x[1])
      .map(([r, n]) => plural(n, ...(REASON_WORDS[r] ?? [r.replace(/_/g, ' '), r.replace(/_/g, ' ')])))
    rows.push({ key: 'review', icon: 'review', tone: 'amber', title: `${plural(a.openReviews, 'decision', 'decisions')} waiting in Review`, sub: parts.join(' · '), to: '/app/review', verb: 'Resolve', primary: true })
  }
  if (can('orders.view') && a.toFulfil) {
    rows.push({
      key: 'fulfil',
      icon: 'orders',
      tone: 'blue',
      title: `${plural(a.toFulfil, 'paid order', 'paid orders')} to pick and ship`,
      sub: `${plural(a.fulfilItems, 'item', 'items')}${a.fulfilOldestShow ? ` · oldest from the ${fmtDate(a.fulfilOldestShow)} show` : ''}`,
      to: '/app/orders?view=queue',
      verb: 'Start picking',
      primary: true,
    })
  }
  if (can('orders.reconcile') && a.unreconciledShows.length) {
    const total = a.unreconciledShows.reduce((n, s) => n + s.pendingCents, 0)
    const dates = a.unreconciledShows.map((s) => s.date)
    rows.push({
      key: 'reconcile',
      icon: 'card',
      tone: 'grey',
      title: `${money(total)} in sales not confirmed yet`,
      sub: `The ${joinDates(dates)} ${dates.length === 1 ? 'show needs its' : 'shows need their'} Seller Hub export`,
      to: `/app/shows/${a.unreconciledShows[a.unreconciledShows.length - 1]!.showId}?tab=payments`,
      verb: 'Reconcile',
    })
  }
  const oversold = a.oversold ?? []
  if (can('inventory.view') && oversold.length) {
    const first = oversold[0]!
    const units = oversold.reduce((n, s) => n + s.short, 0)
    rows.push(
      oversold.length === 1
        ? { key: 'oversold', icon: 'inventory', tone: 'red', title: `Buy ${first.short} more ${first.product}`, sub: `Oversold: ${first.sold} sold, ${plural(first.short, 'order item', 'order items')} not on the shelf yet`, to: '/app/inventory?filter=buy', verb: 'Restock', primary: true }
        : { key: 'oversold', icon: 'inventory', tone: 'red', title: `${oversold.length} products oversold · buy ${units} more`, sub: oversold.slice(0, 3).map((s) => `${s.short} ${s.product}`).join(' · '), to: '/app/inventory?filter=buy', verb: 'Restock', primary: true },
    )
  }
  const low = lowStock.filter((s) => !s.short)
  if (can('inventory.view') && low.length) {
    const first = low[0]!
    rows.push(
      low.length === 1
        ? { key: 'stock', icon: 'inventory', tone: 'red', title: `${first.product} is down to ${first.available}`, sub: `At or below its low-stock line of ${first.lowAt} · ${first.sold} sold`, to: '/app/inventory?filter=low', verb: 'Restock' }
        : { key: 'stock', icon: 'inventory', tone: 'red', title: `${low.length} products are running low`, sub: low.slice(0, 3).map((s) => `${s.product} (${s.available})`).join(' · '), to: '/app/inventory?filter=low', verb: 'Restock' },
    )
  }
  return rows
}

function Sparkline({ values }: { values: number[] }) {
  const w = 168
  const h = 44
  // 3-minute rolling average: the line shows the pace, not per-minute jitter.
  const smooth = values.map((_, i) => {
    const win = values.slice(Math.max(0, i - 2), i + 1)
    return win.reduce((n, v) => n + v, 0) / win.length
  })
  const top = Math.max(1, ...smooth)
  const step = smooth.length > 1 ? w / (smooth.length - 1) : w
  const pts = smooth.map((v, i) => `${(i * step).toFixed(1)} ${(h - 4 - (v / top) * (h - 10)).toFixed(1)}`)
  const line = 'M' + pts.join(' L')
  const total = values.reduce((n, v) => n + v, 0)
  return (
    <figure className="spark">
      <svg role="img" aria-label={`Chat pace: ${total} messages in the last ${values.length} minutes`} width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <path d={`${line} L${w} ${h} L0 ${h} Z`} className="spark-fill" />
        <path d={line} className="spark-line" />
      </svg>
      <figcaption>Chat pace · last {values.length} min</figcaption>
    </figure>
  )
}

function LiveBand({ live, host }: { live: NonNullable<Attention['live']>; host: string | null }) {
  const started = new Date(live.startedAt)
  return (
    <section className="live-band" aria-label="Live show" data-testid="live-card">
      <div className="live-id">
        <div className="live-flag">
          <span className="live-dot" aria-hidden />
          LIVE NOW
        </div>
        <div className="live-title">{weekday(live.date)}’s show</div>
        <div className="live-meta">
          Started {started.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · {duration(live.startedAt, new Date().toISOString())}
          {host ? ` · host ${host}` : ''}
        </div>
      </div>
      <div className="live-stats">
        <div>
          <span>Messages</span>
          <b data-testid="live-messages">{live.messages}</b>
        </div>
        <div>
          <span>Items sold</span>
          <b data-testid="live-items">{live.itemsSold}</b>
        </div>
        <div>
          <span>Buyers</span>
          <b>{live.buyers}</b>
        </div>
        <div>
          <span>Sales so far</span>
          <b>{shortMoney(live.revenueSoldCents ?? live.revenuePaidCents + live.revenuePendingCents)}</b>
        </div>
      </div>
      <div className="live-side">
        <Sparkline values={live.pace} />
        <Link to={`/app/shows/${live.showId}`} className="live-open">
          Open show →
        </Link>
      </div>
    </section>
  )
}

function StandsPill({ s }: { s: ShowTiles }) {
  if (s.live)
    return (
      <span className="pill live">
        <span className="pill-dot" aria-hidden />
        Live
      </span>
    )
  if (s.revenuePendingCents > 0 && !s.toFulfil) return <span className="pill awaiting">Not confirmed</span>
  if (s.toFulfil) return <span className="pill blue">{plural(s.toFulfil, 'order', 'orders')} to pick</span>
  return <span className="pill done">Done</span>
}

/** A show's numbers in the Shows card: Whatnot's when the dashboard shows Whatnot's money
 * (nothing for a recording not matched to a show on Whatnot), else the app's own. */
interface ShowFigures {
  items: number | null
  buyers: number | null
  paid: number
  pending: number
  net?: number | null
  /** Why there are no figures: not on Whatnot, or counted under another recording. */
  why?: string
}

function ShowsCard({ shows, wn }: { shows: ShowTiles[]; wn?: Map<number, WhatnotShowMoney> }) {
  const fig = (s: ShowTiles): ShowFigures => {
    if (!wn) return { items: s.itemsSold, buyers: s.buyers, paid: s.revenuePaidCents, pending: s.revenuePendingCents }
    const w = wn.get(s.showId)
    const none = { items: null, buyers: null, paid: 0, pending: 0, net: null }
    if (!w) return { ...none, why: 'Not matched to a show on Whatnot' }
    if ('sameAsShowId' in w) {
      const first = shows.find((x) => x.showId === w.sameAsShowId)
      return { ...none, why: first ? `Counted under the ${weekdayDate(first.date)} recording of this show` : 'Counted under another recording of this show' }
    }
    return { items: w.items, buyers: w.buyers, paid: w.paidCents, pending: w.pendingCents, net: w.netCents }
  }
  const rows = shows.map((s) => ({ s, f: fig(s) }))
  const pendingWord = wn ? 'payment pending' : 'not confirmed'
  const top = Math.max(1, ...rows.map(({ f }) => f.paid + f.pending))
  const pct = (c: number) => `${Math.max(c ? 1 : 0, Math.round((100 * c) / top))}%`
  const num = (n: number | null) => (n == null ? '—' : n)
  const bar = (f: ShowFigures) => (
    <div className="rev-bar" role="img" aria-label={`${money(f.paid)} paid, ${money(f.pending)} ${pendingWord}`}>
      {f.paid > 0 && <span className="paid" style={{ width: pct(f.paid) }} />}
      {f.pending > 0 && <span className="awaiting" style={{ width: pct(f.pending) }} />}
    </div>
  )
  return (
    <section className="dcard" aria-labelledby="shows-title">
      <div className="dcard-head pad">
        <h2 id="shows-title">Shows</h2>
        <div className="legend">
          <span>
            <i className="sw paid" aria-hidden /> Paid
          </span>
          <span>
            <i className="sw awaiting" aria-hidden /> {wn ? 'Payment pending' : 'Not confirmed'}
          </span>
        </div>
      </div>
      {shows.length === 0 ? (
        <div className="empty inset">
          <b>No shows yet</b>
          Start the Chat Scraper extension on a live show and it will appear here within seconds.
        </div>
      ) : (
        <>
          <div className="table-scroll shows-wide">
            <table className="dtable" data-testid="shows-table">
              <thead>
                <tr>
                  <th scope="col">Show</th>
                  <th scope="col" className="num">
                    Items
                  </th>
                  <th scope="col" className="num">
                    Buyers
                  </th>
                  <th scope="col" className="rev-col">
                    {wn ? 'Sales on Whatnot' : 'Revenue'}
                  </th>
                  {wn && (
                    <th scope="col" className="num">
                      Net payout
                    </th>
                  )}
                  <th scope="col">Where it stands</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ s, f }) => (
                  <tr key={s.showId}>
                    <td>
                      <Link to={`/app/shows/${s.showId}`} className="show-link">
                        {weekdayDate(s.date)}
                      </Link>
                      <div className="muted small">
                        {s.messages} messages · {duration(s.startedAt, s.updatedAt)}
                      </div>
                    </td>
                    <td className="num">{num(f.items)}</td>
                    <td className="num">{num(f.buyers)}</td>
                    <td>
                      {f.items == null ? (
                        <span className="muted small">{f.why}</span>
                      ) : (
                        <div className="rev">
                          {bar(f)}
                          <span className="rev-num">
                            {shortMoney(f.paid)}
                            {f.pending ? <span className="muted"> + {shortMoney(f.pending)}</span> : null}
                          </span>
                        </div>
                      )}
                    </td>
                    {wn && (
                      <td className="num" title={f.net == null && f.items != null ? 'Whatnot’s ledger has no line for this show yet' : undefined}>
                        {f.net == null ? '—' : money(f.net)}
                      </td>
                    )}
                    <td>
                      <StandsPill s={s} />
                      {s.openReviews ? <span className="review-note">{s.openReviews} to review</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="show-cards" aria-label="Shows">
            {rows.map(({ s, f }) => (
              <li key={s.showId}>
                <Link to={`/app/shows/${s.showId}`}>
                  <div className="show-card-head">
                    <b>{weekdayDate(s.date)}</b>
                    <StandsPill s={s} />
                  </div>
                  {f.items == null ? null : bar(f)}
                  <div className="show-card-foot">
                    <span>{f.items == null ? f.why : `${plural(f.items, 'item', 'items')} · ${plural(f.buyers ?? 0, 'buyer', 'buyers')}`}</span>
                    {f.items == null ? null : (
                      <b>
                        {shortMoney(f.paid)} paid{f.pending ? ` + ${shortMoney(f.pending)}` : ''}
                        {f.net != null ? ` · ${money(f.net)} net` : ''}
                      </b>
                    )}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function MoneyCard({ totals }: { totals: Record<string, number> }) {
  const paid = totals.revenuePaidCents ?? 0
  // Paid on Whatnot, product still to pick: shown apart, never added to "Paid" here.
  const toPick = totals.revenueToPickCents ?? 0
  const pending = totals.revenuePendingCents ?? 0
  const sold = paid + pending
  const share = sold ? Math.round((100 * paid) / sold) : 0
  const buyers = totals.buyers ?? 0
  return (
    <section className="dcard money" aria-labelledby="money-title">
      <div data-testid="tile-paid">
        <h2 id="money-title" className="eyebrow">
          Paid revenue · all shows
        </h2>
        <div className="money-big">{money(paid)}</div>
        <div className="muted small">{sold ? `of ${money(sold)} sold · ${share}% collected` : 'no sales yet'}</div>
        {toPick > 0 && (
          <div className="muted small" data-testid="money-to-pick">
            {money(toPick)} more paid on Whatnot, {(totals.toPick ?? 0) === 1 ? 'product' : 'products'} to pick
          </div>
        )}
      </div>
      {sold > 0 && (
        <div>
          <div className="comp-bar" role="img" aria-label={`Revenue: ${money(paid)} paid, ${money(pending)} not confirmed`}>
            {paid > 0 && <span className="paid" style={{ width: `${share}%` }} />}
            {pending > 0 && <span className="awaiting" style={{ width: `${100 - share}%` }} />}
          </div>
          <div className="legend">
            <span>
              <i className="sw paid" aria-hidden /> Paid <b>{money(paid)}</b>
            </span>
            <span>
              <i className="sw awaiting" aria-hidden /> Not confirmed <b>{money(pending)}</b>
            </span>
          </div>
        </div>
      )}
      <div className="stat-grid">
        <div data-testid="tile-items">
          <span>Items sold</span>
          <b>{totals.itemsSold ?? 0}</b>
          <span>{totals.cancelled ?? 0} cancelled or failed</span>
        </div>
        <div data-testid="tile-buyers">
          <span>Buyers</span>
          <b>{buyers}</b>
          <span>{buyers ? `${money(Math.round(paid / buyers))} paid per buyer` : 'no buyers yet'}</span>
        </div>
        <div data-testid="tile-messages">
          <span>Messages</span>
          <b>{totals.messages ?? 0}</b>
          <span>
            {totals.gifts ?? 0} gifts · {totals.noPicks ?? 0} no-picks
          </span>
        </div>
        <div>
          <span>Shows</span>
          <b>{totals.shows ?? 0}</b>
          <span>{totals.toFulfil ?? 0} orders still to ship</span>
        </div>
      </div>
    </section>
  )
}

export function Dashboard() {
  const { can, me } = useAuth()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => apiGet<Overview>('/api/dashboard'), refetchInterval: 30_000 })
  // The money, sales and top buyers as Whatnot states them, for those who may read reports
  // (not the packing role). A shop not connected to Whatnot keeps the app's own numbers.
  const [days, setDays] = useState(30)
  const mayReadMoney = can('reports.view')
  const wq = useQuery({
    queryKey: ['dashboard-whatnot', days],
    queryFn: () => apiGet<WhatnotMoney>(`/api/dashboard/whatnot?days=${days}`),
    enabled: mayReadMoney,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  // Wait for both: the page never shows the app's money and then swaps in Whatnot's.
  if (q.isPending || (mayReadMoney && wq.isPending)) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the dashboard.</p>
  const d = q.data
  const wn = wq.data?.connected ? wq.data : null
  const wnShows = wn ? new Map(wn.shows.map((s) => [s.showId, s])) : undefined
  const rows = attentionRows(d.attention, can, d.lowStock)
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  const ago = Math.max(0, Math.round((Date.now() - q.dataUpdatedAt) / 1000))
  return (
    <section className="dash">
      <header className="dash-head">
        <div>
          <div className="eyebrow">{today}</div>
          <h1 className="display">Dashboard</h1>
          <div className="muted small">
            {plural(d.totals.shows ?? 0, 'show', 'shows')} captured · refreshed {ago < 5 ? 'just now' : `${ago} seconds ago`}
          </div>
        </div>
        {can('reports.view') && (
          <a className="btn ghost" href="/api/reports/overview.xlsx" title="The last 30 days of shows, as an Excel workbook">
            <Icon name="download" size={16} /> Download report
          </a>
        )}
      </header>

      <LiveGaps gaps={d.attention.liveGaps ?? []} link />
      {d.attention.live && <LiveBand live={d.attention.live} host={me?.organization.hostUsername ?? null} />}

      <div className="dash-row">
        <section className="dcard attention-card" aria-labelledby="attn-title">
          <div className="dcard-head pad">
            <h2 id="attn-title">Needs attention</h2>
            {rows.length > 0 && <span className="muted small">{plural(rows.length, 'thing', 'things')} waiting on a person</span>}
          </div>
          {rows.length ? (
            <ul className="attention" data-testid="attention">
              {rows.map((r) => (
                <li key={r.key}>
                  <Link to={r.to} className="attn-row">
                    <span className={`attn-icon tone-${r.tone}`} aria-hidden>
                      <Icon name={r.icon} size={20} />
                    </span>
                    <span className="attn-text">
                      <b>{r.title}</b>
                      <span>{r.sub}</span>
                    </span>
                    <span className={'attn-verb btn' + (r.primary ? '' : ' ghost')}>{r.verb}</span>
                    <Icon name="chevron" className="attn-chev" size={18} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty inset" data-testid="attention-empty">
              <b>All clear</b>
              Nothing is waiting on a person right now.
            </div>
          )}
        </section>
        {wn ? <WhatnotMoneyCard m={wn} chosen={days} onDays={setDays} stale={wq.isPlaceholderData} /> : <MoneyCard totals={d.totals} />}
      </div>
      {wq.isError && <p className="form-error">Could not load Whatnot’s numbers; these are the app’s own.</p>}

      <ShowsCard shows={d.shows} wn={wnShows} />

      <div className="dash-row">
        {wn ? <WhatnotTopBuyers buyers={wn.topBuyers} days={wn.days} /> : <TopBuyers buyers={d.topBuyers} />}
        <Products skus={d.topSkus} />
      </div>
    </section>
  )
}
