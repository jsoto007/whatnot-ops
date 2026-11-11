import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, api, apiGet, money, type WhatnotFigures, type WhatnotLedger as Ledger, type WhatnotRecap as Recap } from '../../lib/api'
import { SHOP_TZ, fmtWhen, plural } from '../../lib/format'
import { ledgerFeeLabel, ledgerKindLabel, resultsStatusLabel } from '../../lib/labels'

const n = (v: number | undefined) => (v == null ? '—' : v.toLocaleString())
const cents = (v: number | undefined) => (v == null ? '—' : money(v))
const signed = (c: number) => (c < 0 ? `−${money(-c)}` : money(c))

/** Whatnot's ledger as Whatnot states it: each kind of line, their sum, and the fees it lists.
 * The app adds up Whatnot's own lines and nothing else; it never works out a net from the fees. */
export function LedgerFigures({ f, testId }: { f: WhatnotFigures; testId: string }) {
  const fees = Object.entries(f.fees).filter(([, c]) => c !== 0)
  return (
    <div data-testid={testId}>
      <table className="table small">
        <tbody>
          {f.kinds.map((k) => (
            <tr key={k.kind}>
              <td>
                {ledgerKindLabel(k.kind)} <span className="muted">· {plural(k.lines, 'line', 'lines')}</span>
              </td>
              <td className="num">{signed(k.cents)}</td>
            </tr>
          ))}
          <tr>
            <td>
              <b>Net, from Whatnot's ledger</b>
            </td>
            <td className="num" data-testid={`${testId}-net`}>
              <b>{signed(f.netCents)}</b>
            </td>
          </tr>
        </tbody>
      </table>
      {fees.length > 0 && (
        <p className="muted small" data-testid={`${testId}-fees`}>
          Fees Whatnot lists on these orders: {fees.map(([k, c]) => `${ledgerFeeLabel(k)} ${money(Math.abs(c))}`).join(' · ')}.{f.costOfGoodsCents != null ? ` Your recorded cost of goods: ${money(f.costOfGoodsCents)}.` : ''}
        </p>
      )}
      {(f.otherCurrencyLines ?? 0) > 0 && (
        <p className="form-error small" data-testid={`${testId}-other-currency`}>
          {plural(f.otherCurrencyLines!, 'line is', 'lines are')} in another currency and not added in here. Check them in Seller Hub.
        </p>
      )}
      {f.through && (
        <p className="muted small">
          Ledger read through {new Date(f.through).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: SHOP_TZ })}. Refunds and carrier adjustments can still arrive later.
        </p>
      )}
    </div>
  )
}

/** A show's Analytics tab: how Whatnot says the show did, and what its ledger says it earned. */
export function WhatnotRecap({ w }: { w: Recap }) {
  const r = w.results
  const tile = (title: string, value: string, note: string, id: string) => (
    <div className="tile" data-testid={`wn-${id}`}>
      <div className="tile-label">{title}</div>
      <div className="tile-value">{value}</div>
      <div className="muted small">{note}</div>
    </div>
  )
  // Only what Whatnot sent: a number it left out is left out here too.
  const more = r
    ? [
        r.showImpressionCount != null && `Shown ${n(r.showImpressionCount)} times${r.showClickthroughRate != null ? `, ${r.showClickthroughRate}% tapped in` : ''}`,
        r.totalViews != null && `${n(r.totalViews)} views`,
        r.buyerSharesCount != null && `${n(r.buyerSharesCount)} shares`,
        r.giveawayOrdersCount != null && `${n(r.giveawayOrdersCount)} giveaways${r.giveawaySpendCents != null ? ` (${money(Math.abs(r.giveawaySpendCents))} in shipping)` : ''}`,
        r.averageRating != null && `rated ${r.averageRating}`,
        w.resultsAt && `read ${fmtWhen(w.resultsAt)}`,
      ].filter((part): part is string => typeof part === 'string')
    : []
  return (
    <div className="card" data-testid="whatnot-recap">
      <h3>On Whatnot{w.title ? ` · ${w.title}` : ''}</h3>
      {r ? (
        <>
          <div className="tiles primary" aria-label="Whatnot's results for the show">
            {tile('Sales', cents(r.salesCents), `${n(r.ordersCount)} orders · ${cents(r.averageOrderValueCents)} each on average`, 'sales')}
            {tile('Estimated earnings', cents(r.estimatedEarningsCents), 'after Whatnot’s fees, as it estimates them', 'earnings')}
            {tile('Buyers', n(r.buyersCount), `${n(r.firstTimeBuyersCount)} first time · ${n(r.returningBuyersCount)} returning`, 'buyers')}
            {tile('Viewers', n(r.distinctViewersCount), `${n(r.maxConcurrentViewers)} at the peak · ${n(r.streamMinutes)} min live`, 'viewers')}
          </div>
          {more.length > 0 && (
            <p className="muted small" data-testid="wn-more">
              {more.join(' · ')}
            </p>
          )}
        </>
      ) : (
        <p className="muted small" data-testid="wn-results-status">
          {w.resultsNotAllowed ? 'The Whatnot access token may not read reports yet, so the show’s results cannot be read. The account owner can add that permission to the token in Seller Hub.' : resultsStatusLabel(w.resultsStatus)}
        </p>
      )}
      <h4>Money, from Whatnot's ledger</h4>
      {w.ledger ? (
        <LedgerFigures f={w.ledger} testId="wn-ledger" />
      ) : (
        <p className="muted small" data-testid="wn-ledger-empty">
          No ledger lines for this show yet. Whatnot's ledger is read once a day; <Link to="/app/reports">Reports</Link> can read it now.
        </p>
      )}
    </div>
  )
}

/** Reports: Whatnot's financial ledger, the last 30 days and the net of each recent show. */
export function WhatnotLedger({ canRead }: { canRead: boolean }) {
  const qc = useQueryClient()
  // While Whatnot builds the file, look again every 20 s: the worker imports it when it is ready.
  const q = useQuery({ queryKey: ['whatnot-ledger'], queryFn: () => apiGet<Ledger>('/api/whatnot/ledger'), refetchInterval: (query) => (query.state.data?.building ? 20_000 : false) })
  const read = useMutation({ mutationFn: () => api<Ledger>('POST', '/api/whatnot/ledger/read'), onSuccess: (d) => qc.setQueryData(['whatnot-ledger'], d) })
  const d = q.data
  if (!d || !d.connected) return null
  const error = read.error instanceof ApiError ? read.error.message : read.error ? 'That did not work. Try again.' : null
  return (
    <div className="card" data-testid="whatnot-ledger">
      <h3>Fees and payouts, from Whatnot</h3>
      <p className="muted small" data-testid="whatnot-ledger-state">
        {d.notAllowed
          ? 'The Whatnot access token may not read reports yet. The account owner can add that permission to the token in Seller Hub.'
          : d.building
            ? 'Whatnot is building the report now. It usually takes a few minutes; this page shows it once it is in.'
            : d.unreadable
              ? 'The last report from Whatnot could not be read. Read it again; if it keeps happening, tell your developer.'
              : d.lines
                ? `${plural(d.lines, 'ledger line', 'ledger lines')} read${d.lastImportedAt ? `, the last ${fmtWhen(d.lastImportedAt)}` : ''}. Whatnot's ledger is read once a day.`
                : 'Nothing read yet. Whatnot’s ledger lists what it credited and charged the shop: earnings, fees, refunds, shipping, tips.'}
      </p>
      {((d.skippedLines ?? 0) > 0 || (d.repeatedLines ?? 0) > 0) && (
        <p className="form-error small" data-testid="whatnot-ledger-short">
          The figures below are short of Whatnot's report:
          {d.skippedLines ? ` ${plural(d.skippedLines, 'line', 'lines')} could not be read.` : ''}
          {d.repeatedLines ? ` ${plural(d.repeatedLines, 'line repeats', 'lines repeat')} a transaction already counted and ${d.repeatedLines === 1 ? 'was' : 'were'} not added again.` : ''} Check the totals against Seller Hub and tell your developer.
        </p>
      )}
      {canRead && !d.notAllowed && (
        <p>
          <button type="button" className="btn" disabled={read.isPending} onClick={() => read.mutate()} data-testid="whatnot-ledger-read">
            {d.building ? 'Check again' : 'Read from Whatnot now'}
          </button>
        </p>
      )}
      {error && <p className="form-error small">{error}</p>}
      {d.recent && (
        <>
          <h4>The last {d.days} days</h4>
          <LedgerFigures f={d.recent} testId="ledger-recent" />
        </>
      )}
      {(d.shows?.length ?? 0) > 0 && (
        <>
          <h4>By show</h4>
          <table className="table small" data-testid="ledger-shows">
            <thead>
              <tr>
                <th>Show</th>
                <th>Started</th>
                <th className="num">Net, from Whatnot's ledger</th>
              </tr>
            </thead>
            <tbody>
              {d.shows!.map((s, i) => (
                <tr key={i}>
                  <td>{s.showId ? <Link to={`/app/shows/${s.showId}?tab=analytics`}>{s.title || 'Untitled show'}</Link> : s.title || 'Untitled show'}</td>
                  <td>{s.startedAt ? new Date(s.startedAt).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) : '—'}</td>
                  <td className="num">{signed(s.netCents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
