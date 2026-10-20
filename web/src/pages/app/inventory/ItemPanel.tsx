import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Sheet } from '../../../components/Sheet'
import { ApiError, api, apiGet, money, type Activity, type StockLevel } from '../../../lib/api'
import { fmtWhen } from '../../../lib/format'
import { describe, stateOf } from './ledger'

const ADD_REASONS = ['Received', 'Found', 'Returned']
const REMOVE_REASONS = ['Damaged', 'Giveaway', 'Lost', 'Correction']

/** One item: where it stands, quick add / remove with a reason, its alert line, its history. */
export function ItemPanel({ sku, write, onClose, onChanged, onReceive }: { sku: StockLevel; write: boolean; onClose: () => void; onChanged: (msg: string) => Promise<unknown>; onReceive: (qty: number) => void }) {
  const [dir, setDir] = useState<'add' | 'remove'>('add')
  const [qty, setQty] = useState('')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [lowAt, setLowAt] = useState(String(sku.lowStockAt))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const history = useQuery({ queryKey: ['movements', sku.id], queryFn: () => apiGet<{ movements: Activity[] }>(`/api/inventory/${sku.id}/movements`) })
  const st = stateOf(sku)
  const reasons = dir === 'add' ? ADD_REASONS : REMOVE_REASONS

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await onChanged(msg)
      await history.refetch()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Try again.')
    } finally {
      setBusy(false)
    }
  }
  const applyAdjust = () => {
    const n = /^\s*\d+\s*$/.test(qty) ? parseInt(qty, 10) : NaN
    if (!(n > 0)) return setError('Enter how many, 1 or more')
    const why = [reason, note.trim()].filter(Boolean).join(': ') || undefined
    void run(async () => {
      await api('POST', `/api/inventory/${sku.id}/adjust`, { delta: dir === 'add' ? n : -n, reason: why })
      setQty('')
      setNote('')
      setReason('')
    }, `${dir === 'add' ? 'Added' : 'Removed'} ${n} ${sku.productName}`)
  }

  return (
    <Sheet
      title={`${sku.productName}${sku.name ? ` ${sku.name}` : ''}`}
      sub={
        <>
          <code>{sku.code}</code>
          {sku.priceCents != null && <> · {money(sku.priceCents)}</>}
          {sku.aliases.length > 0 && <> · chat calls it {sku.aliases.map((a) => `“${a.alias}”`).join(', ')}</>}
        </>
      }
      onClose={onClose}
    >
      <div className="item-numbers">
        <div>
          <span className="tile-label">On shelf</span>
          <b data-testid="panel-on-hand">{sku.onHand}</b>
        </div>
        <div>
          <span className="tile-label">Held for sales</span>
          <b>{sku.reserved}</b>
        </div>
        <div>
          <span className="tile-label">Available</span>
          <b className={sku.short ? 'neg' : undefined} data-testid="panel-available">
            {sku.available}
          </b>
        </div>
        <div>
          <span className="tile-label">Sold</span>
          <b>{sku.sold}</b>
        </div>
      </div>
      <p className={`item-state tone-${st.tone}`}>
        <b>{st.label}.</b>{' '}
        {sku.short > 0
          ? `Buyers are owed ${sku.short} more than the shelf holds. Receive at least ${sku.short} to fill every order.`
          : sku.low
            ? `At or below the alert line of ${sku.lowStockAt}.`
            : `${sku.available} free to sell; alert at ${sku.lowStockAt}.`}
        {write && sku.short > 0 && (
          <button type="button" className="btn small primary" onClick={() => onReceive(sku.short)}>
            Receive {sku.short}
          </button>
        )}
      </p>

      {write && (
        <section className="item-section" aria-label="Add or remove stock">
          <h4>Add or remove</h4>
          <div className="seg" role="group" aria-label="Direction">
            <button type="button" className={dir === 'add' ? 'on' : ''} aria-pressed={dir === 'add'} onClick={() => (setDir('add'), setReason(''))}>
              Add
            </button>
            <button type="button" className={dir === 'remove' ? 'on' : ''} aria-pressed={dir === 'remove'} onClick={() => (setDir('remove'), setReason(''))}>
              Remove
            </button>
          </div>
          <form
            className="adjust-form"
            onSubmit={(e) => {
              e.preventDefault()
              applyAdjust()
            }}
          >
            <input value={qty} onChange={(e) => setQty(e.target.value)} inputMode="numeric" placeholder="How many" aria-label={`How many to ${dir}`} size={8} />
            <div className="reason-chips" role="group" aria-label="Reason">
              {reasons.map((r) => (
                <button type="button" key={r} className={'chip-btn' + (reason === r ? ' on' : '')} aria-pressed={reason === r} onClick={() => setReason(reason === r ? '' : r)}>
                  {r}
                </button>
              ))}
            </div>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Note" maxLength={160} className="grow" />
            <button type="submit" className="btn primary" disabled={busy}>
              {dir === 'add' ? 'Add' : 'Remove'}
              {qty.trim() ? ` ${qty.trim()}` : ''}
            </button>
          </form>
          <form
            className="alert-form"
            onSubmit={(e) => {
              e.preventDefault()
              const n = /^\s*\d+\s*$/.test(lowAt) ? parseInt(lowAt, 10) : NaN
              if (Number.isNaN(n)) return setError('The alert line is a whole number, 0 or more')
              void run(() => api('PATCH', `/api/skus/${sku.id}`, { lowStockAt: n }), `${sku.productName} alerts at ${n} now`)
            }}
          >
            <label htmlFor={`low-${sku.id}`}>Warn me when available drops to</label>
            <input id={`low-${sku.id}`} value={lowAt} onChange={(e) => setLowAt(e.target.value)} inputMode="numeric" size={4} />
            <button type="submit" className="btn small" disabled={busy || lowAt === String(sku.lowStockAt)}>
              Save
            </button>
          </form>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </section>
      )}

      <section className="item-section" aria-label="History">
        <h4>History</h4>
        {history.isPending ? (
          <p className="muted small">Loading…</p>
        ) : history.isError || history.data.movements.length === 0 ? (
          <p className="muted small">Nothing has moved yet.</p>
        ) : (
          <ol className="ledger" data-testid="item-history">
            {history.data.movements.map((m) => {
              const d = describe(m)
              return (
                <li key={m.id} className={`ledger-row tone-${d.tone}`}>
                  <span className="ledger-dot" aria-hidden="true" />
                  <div className="ledger-main">
                    <div>{d.text}</div>
                    <div className="muted small">
                      {fmtWhen(m.at)} · {m.actor} · {m.onHandAfter} on shelf, {m.reservedAfter} held
                    </div>
                  </div>
                  <span className="ledger-effect">{d.effect}</span>
                </li>
              )
            })}
          </ol>
        )}
      </section>
    </Sheet>
  )
}
