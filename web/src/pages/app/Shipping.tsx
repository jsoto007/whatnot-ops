import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { Icon } from '../../components/Icon'
import { BuyerFlagChip } from '../../components/BuyerFlag'
import { ApiError, api, apiFile, apiGet, type Shipping as Doc, type ShippingBuyer, type ShippingPackage } from '../../lib/api'
import { fmtLongDate, fmtWhen, plural } from '../../lib/format'
import { labelProblemLabel, orderStatusLabel, packageHoldLabel, packageStatusLabel, shippingStateLabel } from '../../lib/labels'

const CONFIRM = (n: number) =>
  `Make ${plural(n, 'label', 'labels')}?\n\nEach label closes its buyer's bundle on Whatnot: anything they win afterwards ships, and is charged shipping, separately. It cannot be undone.`
/** Whether "Print labels" includes Whatnot's packing slips: kept on this laptop, like the printer it goes to. */
const SLIPS_KEY = 'wn.labelSlips'
const readSlips = (): boolean => {
  try {
    return localStorage.getItem(SLIPS_KEY) !== 'no'
  } catch {
    return true
  }
}
const saveSlips = (on: boolean) => {
  try {
    localStorage.setItem(SLIPS_KEY, on ? 'yes' : 'no')
  } catch {
    /* private window or storage off: the choice lasts for this visit */
  }
}
const fmtDay = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })

/**
 * A show's shipping, from Whatnot: one package and one label per buyer, in the order the slips are
 * packed (most items first). Whatnot keeps a buyer's wins in one bundle until its label is made, so
 * labels are made after the show; a buyer in two open packages is called out, never given two labels.
 */
export function Shipping() {
  const { showId = '' } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const key = ['shipping', showId]
  const q = useQuery({ queryKey: key, queryFn: () => apiGet<Doc>(`/api/shipping/shows/${showId}`) })
  const [note, setNote] = useState<string | null>(null)
  const [withSlips, setWithSlips] = useState(readSlips)
  const [printing, setPrinting] = useState(false)
  const [printError, setPrintError] = useState<string | null>(null)
  /** The PDF of the last print, for a few minutes: a way back to it when its tab was closed. */
  const [lastPdf, setLastPdf] = useState<string | null>(null)
  /** The show's next labels not printed yet, as one PDF in the order of the order slips. */
  const printLabels = async () => {
    // Opened inside the click, so the browser allows it; the PDF goes into it once it arrives.
    const tab = window.open('', '_blank')
    if (!tab) {
      // Asking the server now would mark the labels printed with nothing on screen.
      setPrintError('The browser blocked the new tab for the labels. Allow pop-ups for this site, then press Print labels again.')
      return
    }
    setPrinting(true)
    setPrintError(null)
    try {
      const r = await apiFile(`/api/shipping/shows/${showId}/labels/print`, { slips: withSlips })
      const url = URL.createObjectURL(r.blob)
      tab.location.href = url
      setLastPdf(url)
      setTimeout(() => {
        URL.revokeObjectURL(url)
        setLastPdf((u) => (u === url ? null : u))
      }, 5 * 60_000)
      const n = (h: string) => Number(r.headers.get(h) ?? 0)
      const [printed, failed, left] = [n('X-Labels-Printed'), n('X-Labels-Failed'), n('X-Labels-Left')]
      setNote(
        `Opened ${plural(printed, 'label', 'labels')} to print.` +
          (failed ? ` Whatnot did not send ${plural(failed, 'label', 'labels')}: try again in a minute.` : '') +
          (left ? ` ${plural(left, 'label is', 'labels are')} still to print.` : ''),
      )
      await qc.invalidateQueries({ queryKey: key })
    } catch (e) {
      tab.close()
      setPrintError(e instanceof ApiError ? e.message : 'The labels could not be opened. Try again.')
    } finally {
      setPrinting(false)
    }
  }
  const act = useMutation({
    mutationFn: (a: { path: 'refresh' | 'labels' | 'mark-shipped'; body?: unknown }) => api<Doc>('POST', `/api/shipping/shows/${showId}/${a.path}`, a.body ?? {}),
    onSuccess: async (d, a) => {
      qc.setQueryData(key, d)
      if (a.path === 'labels') setNote(d.requested ? `Asked Whatnot for ${plural(d.requested, 'label', 'labels')}. They appear here as Whatnot finishes each one.` : 'No package was ready for a label.')
      else if (a.path === 'mark-shipped') {
        setNote(d.marked ? `${plural(d.marked, 'order', 'orders')} marked shipped.` : 'Nothing to mark shipped.')
        await qc.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'shipping' })
      } else setNote(d.queued ? 'Reading from Whatnot now. This page shows the result in a minute.' : null)
    },
  })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load shipping for this show.</p>
  const d = q.data
  const t = d.totals
  const ready = (t.ready ?? 0) + (t.second ?? 0)
  // What the button would send, as the server counts it: never a package already asked for.
  const toMake = d.labelsToMake ?? 0
  const toPrint = d.labelsToPrint ?? 0
  const error = printError ?? (act.error instanceof ApiError ? act.error.message : act.error ? 'That did not work. Try again.' : null)

  return (
    <section className="shipping">
      <p>
        <Link to={`/app/orders?view=all&show=${showId}`}>← Orders</Link>
      </p>
      <div className="page-head">
        <div>
          <h2>Shipping{d.showDate ? ` · ${fmtLongDate(d.showDate)}` : ''}</h2>
          <div className="sub">One package and one label per buyer. Whatnot keeps everything a buyer wins in one bundle until its label is made.</div>
        </div>
        <div className="head-actions">
          <Link className="btn ghost" to={`/app/orders/slips/${showId}`}>
            <Icon name="print" size={16} />
            Order slips
          </Link>
        </div>
      </div>

      {!d.connected ? (
        <div className="empty" data-testid="shipping-state">
          <b>Not connected to Whatnot</b>
          Packages, labels and tracking come from Whatnot once the shop's owner connects it in Settings.
        </div>
      ) : !d.matched ? (
        <div className="empty" data-testid="shipping-state">
          <b>This show is not matched to a show on Whatnot yet</b>
          <Link to={`/app/shows/${showId}?tab=payments`}>Match it on the show's Payments tab</Link>
        </div>
      ) : (
        <>
          <p className="small" data-testid="shipping-state">
            <b>{d.show!.title || 'Untitled show'}</b> · {plural(t.buyers ?? 0, 'buyer', 'buyers')}
            {ready ? ` · ${ready} ready for a label` : ''}
            {t.labelled ? ` · ${t.labelled} with a label${toPrint ? ` (${toPrint} to print)` : ''}` : ''}
            {t.shipped ? ` · ${t.shipped} with the carrier` : ''}
            {t.split ? ` · ${t.split} to combine` : ''}
            {t.hold ? ` · ${t.hold} on hold` : ''}
            {t.none ? ` · ${t.none} not packaged yet` : ''}
            {t.pickup ? ` · ${t.pickup} for pickup` : ''}
          </p>
          {!d.show!.ended ? (
            <p className="card small" data-testid="shipping-live">
              The show has not ended. Labels wait until it does: making one closes that buyer's bundle, and anything they win afterwards would ship separately.
            </p>
          ) : (
            !d.show!.complete && (
              <p className="card small" data-testid="shipping-reading">
                The show has ended, but its final orders and packages have not been read from Whatnot yet. Press <b>Read again from Whatnot</b> before making labels, so a last win is not left out of its
                buyer's package.
              </p>
            )
          )}
          <div className="inline-form">
            {can('orders.fulfill') && (
              <button type="button" className="btn" disabled={act.isPending} onClick={() => act.mutate({ path: 'refresh' })} data-testid="shipping-refresh">
                Read again from Whatnot
              </button>
            )}
            {can('orders.labels') && (
              <button
                type="button"
                className="btn primary"
                disabled={act.isPending || !d.show!.ended || toMake === 0}
                onClick={() => {
                  if (window.confirm(CONFIRM(toMake))) act.mutate({ path: 'labels', body: { confirm: true } })
                }}
                data-testid="shipping-make-labels"
              >
                {toMake ? `Make ${plural(toMake, 'label', 'labels')}` : 'No labels to make'}
              </button>
            )}
            {can('orders.fulfill') && toPrint > 0 && (
              <button type="button" className={toMake ? 'btn' : 'btn primary'} disabled={printing} onClick={() => void printLabels()} data-testid="shipping-print-labels">
                <Icon name="print" size={16} />
                {printing ? 'Getting the labels…' : `Print ${plural(toPrint, 'label', 'labels')}`}
              </button>
            )}
            {can('orders.fulfill') && (d.toMarkShipped ?? 0) > 0 && (
              <button type="button" className="btn" disabled={act.isPending} onClick={() => act.mutate({ path: 'mark-shipped' })} data-testid="shipping-mark-shipped">
                Mark {plural(d.toMarkShipped!, 'order', 'orders')} shipped
              </button>
            )}
          </div>
          {note && (
            <p className="small" role="status" data-testid="shipping-note">
              {note}
            </p>
          )}
          {error && <p className="form-error small">{error}</p>}
          {can('orders.fulfill') && (toPrint > 0 || (d.labelsPrinted ?? 0) > 0) && (
            <p className="small muted" data-testid="shipping-print-state">
              {toPrint > 0 ? (
                <label className="field check">
                  <input
                    type="checkbox"
                    checked={withSlips}
                    onChange={(e) => {
                      setWithSlips(e.target.checked)
                      saveSlips(e.target.checked)
                    }}
                  />
                  <span>Print Whatnot’s packing slip with each label. Labels come out in the order of the order slips, and one already printed is skipped.</span>
                </label>
              ) : (
                `Every label of this show has been printed (${d.labelsPrinted}). One can be printed again from its row.`
              )}
              {lastPdf && (
                <>
                  {' '}
                  <a href={lastPdf} target="_blank" rel="noreferrer" data-testid="shipping-open-pdf">
                    Open the last PDF again
                  </a>
                </>
              )}
            </p>
          )}
          {d.buyers.length === 0 ? (
            <div className="empty">
              <b>No packages yet</b>
              They appear as Whatnot's orders for this show are read.
            </div>
          ) : (
            <div className="card flush">
              <table className="table" data-testid="shipping-buyers">
                <thead>
                  <tr>
                    <th>Buyer</th>
                    <th className="num">Items</th>
                    <th>Shipping</th>
                    <th>Package</th>
                    <th>Order here</th>
                  </tr>
                </thead>
                <tbody>
                  {d.buyers.map((b, i) => (
                    <BuyerRow key={b.buyer ?? i} b={b} canPrint={can('orders.fulfill')} onPrinted={() => setTimeout(() => void qc.invalidateQueries({ queryKey: key }), 1500)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function BuyerRow({ b, canPrint, onPrinted }: { b: ShippingBuyer; canPrint: boolean; onPrinted: () => void }) {
  const warn = b.state === 'split' || b.state === 'hold' || b.state === 'failed' || b.state === 'other'
  return (
    <tr data-testid={`ship-${b.buyer}`} className={b.state === 'shipped' ? 'low' : ''}>
      <td className="c-buyer">
        {b.buyer ?? '—'}
        <BuyerFlagChip name={b.buyer} />
      </td>
      <td className="num">{b.items}</td>
      <td>
        <span className={warn ? 'form-error' : ''} data-testid="ship-state">
          {shippingStateLabel(b.state)}
        </span>
        {b.unplaced > 0 && b.packages.length > 0 && <div className="muted small">{plural(b.unplaced, 'item is', 'items are')} not in a package yet</div>}
      </td>
      <td>
        {b.packages.length === 0 ? <span className="muted">—</span> : b.packages.map((p) => <Package key={p.id} p={p} canPrint={canPrint} many={b.packages.length > 1} onPrinted={onPrinted} />)}
      </td>
      <td>{b.orderId ? <Link to={`/app/orders/${b.orderId}`}>{orderStatusLabel(b.orderStatus ?? '')}</Link> : <span className="muted">no order here</span>}</td>
    </tr>
  )
}

function Package({ p, canPrint, many, onPrinted }: { p: ShippingPackage; canPrint: boolean; many: boolean; onPrinted: () => void }) {
  return (
    <div className="ship-package" data-testid="ship-package">
      <div>
        {packageStatusLabel(p.status)}
        {many ? ` · ${plural(p.items, 'item', 'items')}` : ''}
        {p.pickup ? ' · pickup' : ''}
        {p.tracking && (
          <span className="muted small">
            {' '}
            · {p.carrier ? `${p.carrier} ` : ''}
            {p.tracking}
          </span>
        )}
      </div>
      {p.holds.map((h) => (
        <div key={h} className="form-error small">
          {packageHoldLabel(h)}
        </div>
      ))}
      {p.otherItems > 0 && (
        <div className="small">
          Also holds {plural(p.otherItems, 'item', 'items')} from another show{p.liveElsewhere ? ' that is live now: wait for it to end before making the label.' : ': pack them together.'}
        </div>
      )}
      {p.labelProblem ? (
        <div className="form-error small">{labelProblemLabel(p.labelProblem)}.</div>
      ) : (
        p.status === 'created' && p.labelRequestedAt && <div className="muted small">Label asked for {fmtWhen(p.labelRequestedAt)}; Whatnot is making it.</div>
      )}
      {p.dueAt && (p.status === 'created' || p.status === 'label_generated') && <div className="muted small">Ship by {fmtDay(p.dueAt)}</div>}
      {p.printedAt && (
        <div className="muted small" data-testid="ship-printed">
          Label printed {fmtWhen(p.printedAt)}
        </div>
      )}
      <div className="ship-links small">
        {canPrint && p.labelReady && (
          <a href={`/api/shipping/shipments/${p.id}/label.pdf`} target="_blank" rel="noreferrer" data-testid="ship-print" onClick={onPrinted}>
            {p.printedAt ? 'Print label and slip again' : 'Print label and slip'}
          </a>
        )}
        {p.url && (
          <a href={p.url} target="_blank" rel="noreferrer">
            Open in Seller Hub
          </a>
        )}
      </div>
    </div>
  )
}
