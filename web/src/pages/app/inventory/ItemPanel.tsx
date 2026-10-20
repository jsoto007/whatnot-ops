import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Icon } from '../../../components/Icon'
import { Sheet } from '../../../components/Sheet'
import { ApiError, api, apiGet, money, type Activity, type StockLevel } from '../../../lib/api'
import { fmtWhen } from '../../../lib/format'
import { describe, stateOf } from './ledger'

type Dir = 'add' | 'remove'
const REASONS: Record<Dir, string[]> = {
  add: ['Received', 'Found', 'Returned', 'Correction'],
  remove: ['Damaged', 'Giveaway', 'Lost', 'Correction'],
}
const toN = (s: string) => (/^\d+$/.test(s.trim()) ? parseInt(s, 10) : 0)

/** A number with − / + buttons; digits only, never below `min`. */
function Stepper({ id, value, onChange, label, min = 0, size = 'm' }: { id: string; value: string; onChange: (v: string) => void; label: string; min?: number; size?: 'm' | 's' }) {
  const n = toN(value)
  return (
    <div className={`stepper ${size}`}>
      <button type="button" onClick={() => onChange(String(Math.max(min, n - 1)))} disabled={n <= min} aria-label={`${label}: one fewer`}>
        <Icon name="minus" size={16} />
      </button>
      <input id={id} value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))} onFocus={(e) => e.target.select()} inputMode="numeric" aria-label={label} />
      <button type="button" onClick={() => onChange(String(n + 1))} aria-label={`${label}: one more`}>
        <Icon name="plus" size={16} />
      </button>
    </div>
  )
}

/** One item: where it stands, add / remove with a reason, its alert line, its history. */
export function ItemPanel({ sku, write, onClose, onChanged, onReceive }: { sku: StockLevel; write: boolean; onClose: () => void; onChanged: (msg: string) => Promise<unknown>; onReceive: (qty: number) => void }) {
  const [dir, setDir] = useState<Dir>('add')
  const [qty, setQty] = useState('1')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [lowAt, setLowAt] = useState(String(sku.lowStockAt))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const history = useQuery({ queryKey: ['movements', sku.id], queryFn: () => apiGet<{ movements: Activity[] }>(`/api/inventory/${sku.id}/movements`) })
  const st = stateOf(sku)

  const n = toN(qty)
  const delta = dir === 'add' ? n : -n
  const shelf = Math.max(0, sku.onHand)
  const tooMany = dir === 'remove' && n > shelf
  const afterShelf = sku.onHand + delta
  const afterAvail = sku.available + delta
  const owedAfter = dir === 'remove' && !tooMany && afterAvail < 0 && afterAvail < sku.available ? -afterAvail : 0
  const lowAtN = toN(lowAt)
  const lowChanged = lowAt.trim() !== '' && lowAtN !== sku.lowStockAt

  const switchDir = (d: Dir) => {
    setDir(d)
    setReason('')
    setError(null)
  }
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
    if (n < 1 || tooMany) return
    const why = [reason, note.trim()].filter(Boolean).join(': ') || undefined
    void run(async () => {
      await api('POST', `/api/inventory/${sku.id}/adjust`, { delta, reason: why })
      setQty('1')
      setNote('')
      setReason('')
    }, `${dir === 'add' ? 'Added' : 'Removed'} ${n} ${sku.productName} · ${afterShelf} on shelf`)
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
        <div title="Held for open sales; leaves the shelf when paid">
          <span className="tile-label">Held</span>
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
        <span>
          <b>{st.label}.</b>{' '}
          {sku.short > 0
            ? `Buyers are owed ${sku.short} more than the shelf holds.`
            : sku.low
              ? `At or below the alert line of ${sku.lowStockAt}.`
              : `${sku.available} free to sell.`}
        </span>
        {write && sku.short > 0 && (
          <button type="button" className="btn small primary" onClick={() => onReceive(sku.short)}>
            Receive {sku.short}
          </button>
        )}
      </p>

      {write && (
        <section className="item-section" aria-labelledby={`adj-${sku.id}`}>
          <h4 id={`adj-${sku.id}`}>Change stock</h4>
          <form
            className={`adjust-card is-${dir}`}
            onSubmit={(e) => {
              e.preventDefault()
              applyAdjust()
            }}
          >
            <div className="dir-toggle" role="radiogroup" aria-label="Add or remove">
              {(['add', 'remove'] as const).map((d) => (
                <button key={d} type="button" role="radio" aria-checked={dir === d} className={dir === d ? `on ${d}` : ''} onClick={() => switchDir(d)}>
                  <Icon name={d === 'add' ? 'plus' : 'minus'} size={16} />
                  {d === 'add' ? 'Add stock' : 'Remove stock'}
                </button>
              ))}
            </div>

            <div className="adjust-qty">
              <label htmlFor={`qty-${sku.id}`}>How many</label>
              <Stepper id={`qty-${sku.id}`} value={qty} onChange={setQty} label={`How many to ${dir}`} />
              <div className="adjust-preview" aria-live="polite" data-testid="adjust-preview">
                {tooMany ? (
                  <span className="warn-text">Only {shelf} on the shelf</span>
                ) : n > 0 ? (
                  <>
                    <span className="muted">On shelf</span> <b>{sku.onHand}</b> <Icon name="chevron" size={12} /> <b className={dir === 'add' ? 'up' : 'down'}>{afterShelf}</b>
                    {owedAfter > 0 && <div className="warn-text small">Leaves {owedAfter} owed to open sales</div>}
                  </>
                ) : (
                  <span className="muted">Enter an amount</span>
                )}
              </div>
            </div>

            <div className="adjust-reason">
              <span className="adjust-label" id={`why-${sku.id}`}>
                Reason <span className="muted">(optional)</span>
              </span>
              <div className="reason-chips" role="group" aria-labelledby={`why-${sku.id}`}>
                {REASONS[dir].map((r) => (
                  <button type="button" key={r} className={'chip-btn' + (reason === r ? ' on' : '')} aria-pressed={reason === r} onClick={() => setReason(reason === r ? '' : r)}>
                    {r}
                  </button>
                ))}
              </div>
              <input className="adjust-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note, e.g. box 3 was crushed" aria-label="Note" maxLength={160} />
            </div>

            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className={'btn block ' + (dir === 'add' ? 'primary' : 'danger-solid')} disabled={busy || n < 1 || tooMany}>
              {busy ? 'Saving…' : dir === 'add' ? `Add ${n || ''} to the shelf` : `Remove ${n || ''} from the shelf`}
            </button>
          </form>
        </section>
      )}

      {write && (
        <section className="item-section" aria-labelledby={`alert-${sku.id}`}>
          <h4 id={`alert-${sku.id}`}>Low-stock alert</h4>
          <form
            className="alert-row"
            onSubmit={(e) => {
              e.preventDefault()
              if (!lowChanged) return
              void run(() => api('PATCH', `/api/skus/${sku.id}`, { lowStockAt: lowAtN }), `${sku.productName} alerts at ${lowAtN} now`)
            }}
          >
            <label htmlFor={`low-${sku.id}`}>Warn me when available drops to</label>
            <Stepper id={`low-${sku.id}`} value={lowAt} onChange={setLowAt} label="Warn me when available drops to" size="s" />
            {lowChanged && (
              <button type="submit" className="btn small primary" disabled={busy}>
                Save
              </button>
            )}
          </form>
          <p className="muted small alert-hint">{sku.low ? `Showing now: ${sku.available} available.` : `${sku.available} available now; the alert shows on the dashboard at ${sku.lowStockAt} or fewer.`}</p>
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
