import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type Show } from '../../lib/api'
import { SHOP_TZ, plural } from '../../lib/format'

/** As the API enforces them (api/app/routes/reports.py). */
const OVERVIEW_DAYS = 30
const MAX_OVERVIEW_SHOWS = 12
/** YYYY-MM-DD in the shop's zone, `n` days back. */
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toLocaleDateString('en-CA', { timeZone: SHOP_TZ })

export function Reports() {
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  const [showId, setShowId] = useState('')
  // The last 30 days to start with: a workbook of every show ever made is too big to build at once.
  const [from, setFrom] = useState(() => daysAgo(OVERVIEW_DAYS))
  const [to, setTo] = useState('')
  const inRange = (shows.data?.shows ?? []).filter((s) => (!from || s.date >= from) && (!to || s.date <= to)).length
  const tooMany = inRange > MAX_OVERVIEW_SHOWS
  return (
    <section>
      <h2>Reports</h2>
      <p className="muted">
        Excel workbooks with these sheets: sales by show, customers, products sold, inventory remaining, cancellations and failed
        payments, revenue, fulfilment list, stock movements.
      </p>
      <div className="card">
        <h3>One show</h3>
        <div className="inline-form">
          <label className="field">
            <span>Show</span>
            <select value={showId} onChange={(e) => setShowId(e.target.value)} aria-label="Report show">
              <option value="">Choose…</option>
              {shows.data?.shows.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.date} · {s.messageCount} messages
                </option>
              ))}
            </select>
          </label>
          <a className={'btn primary' + (showId ? '' : ' disabled')} href={showId ? `/api/reports/show/${showId}.xlsx` : undefined} data-testid="download-show" aria-disabled={!showId}>
            Download .xlsx
          </a>
        </div>
      </div>
      <div className="card">
        <h3>All shows in a date range</h3>
        <div className="inline-form">
          <label className="field">
            <span>From</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <a
            className={'btn primary' + (tooMany ? ' disabled' : '')}
            href={tooMany ? undefined : `/api/reports/overview.xlsx?from=${from}&to=${to}`}
            data-testid="download-overview"
            aria-disabled={tooMany}
          >
            Download .xlsx
          </a>
        </div>
        <p className={tooMany ? 'form-error small' : 'muted small'} data-testid="overview-range">
          {tooMany
            ? `These dates cover ${inRange} shows. One report holds up to ${MAX_OVERVIEW_SHOWS}: pick a shorter range.`
            : `${plural(inRange, 'show', 'shows')} in these dates. One report holds up to ${MAX_OVERVIEW_SHOWS} shows.`}
        </p>
      </div>
    </section>
  )
}
