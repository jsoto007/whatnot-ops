import { useMemo, useState } from 'react'
import { Sheet } from '../../../components/Sheet'
import { Icon } from '../../../components/Icon'
import { ApiError, api, type ReceiveResult, type StockLevel } from '../../../lib/api'
import { plural, requestId } from '../../../lib/format'

type Line =
  | { key: number; mode: 'existing'; skuId: string; qty: string }
  | { key: number; mode: 'new'; name: string; code: string; price: string; lowAt: string; qty: string }

let nextKey = 1
const blank = (): Line => ({ key: nextKey++, mode: 'existing', skuId: '', qty: '' })
const fresh = (): Line => ({ key: nextKey++, mode: 'new', name: '', code: '', price: '', lowAt: '', qty: '' })

const toInt = (s: string) => (/^\s*\d+\s*$/.test(s) ? parseInt(s, 10) : NaN)
/** "12.50" → 1250 cents; blank → null; anything else → NaN. */
const toCents = (s: string) => (s.trim() === '' ? null : /^\s*\d+(\.\d{1,2})?\s*$/.test(s) ? Math.round(parseFloat(s) * 100) : NaN)

/** Receive a delivery: several known items and brand-new ones in one go. */
export function Receive({ skus, preset, onClose, onDone }: { skus: StockLevel[]; preset?: { skuId: number; qty: number }[]; onClose: () => void; onDone: (r: ReceiveResult) => void }) {
  const [lines, setLines] = useState<Line[]>(() => (preset?.length ? preset.map((p) => ({ key: nextKey++, mode: 'existing' as const, skuId: String(p.skuId), qty: String(p.qty) })) : [blank()]))
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [receiptId] = useState(requestId)
  const byId = useMemo(() => new Map(skus.map((s) => [String(s.id), s])), [skus])
  const sorted = useMemo(() => skus.filter((s) => s.active).slice().sort((a, b) => a.productName.localeCompare(b.productName) || a.code.localeCompare(b.code)), [skus])

  const set = (key: number, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? ({ ...l, ...patch } as Line) : l)))
  const remove = (key: number) => setLines((ls) => (ls.length > 1 ? ls.filter((l) => l.key !== key) : ls))
  const filled = lines.filter((l) => l.qty.trim() !== '' || (l.mode === 'existing' ? l.skuId : l.name.trim()))
  const units = filled.reduce((n, l) => n + (toInt(l.qty) || 0), 0)
  const chosen = new Set(lines.flatMap((l) => (l.mode === 'existing' && l.skuId ? [l.skuId] : [])))

  const submit = async () => {
    setError(null)
    const payload = []
    for (const [i, l] of filled.entries()) {
      const qty = toInt(l.qty)
      const label = `Line ${i + 1}`
      if (!(qty > 0)) return setError(`${label}: enter how many arrived`)
      if (l.mode === 'existing') {
        if (!l.skuId) return setError(`${label}: pick an item`)
        payload.push({ skuId: Number(l.skuId), quantity: qty })
      } else {
        if (!l.name.trim()) return setError(`${label}: the new item needs a name`)
        const price = toCents(l.price)
        if (Number.isNaN(price)) return setError(`${label}: price looks wrong (use 12 or 12.50)`)
        const lowAt = l.lowAt.trim() === '' ? undefined : toInt(l.lowAt)
        if (Number.isNaN(lowAt)) return setError(`${label}: low-stock alert must be a whole number`)
        payload.push({ new: { name: l.name.trim(), code: l.code.trim() || undefined, priceCents: price, lowStockAt: lowAt }, quantity: qty })
      }
    }
    if (!payload.length) return setError('Add at least one item')
    setBusy(true)
    try {
      onDone(await api<ReceiveResult>('POST', '/api/inventory/receive', { receiptId, note: note.trim() || undefined, lines: payload }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      title="Receive stock"
      sub="Add what just arrived. New items are created as you receive them."
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted small" data-testid="receive-total">
            {plural(filled.length, 'item', 'items')} · {plural(units, 'unit', 'units')}
          </span>
          <button type="button" className="btn primary" onClick={submit} disabled={busy || units === 0} data-testid="receive-submit">
            {busy ? 'Saving…' : `Receive ${plural(units, 'unit', 'units')}`}
          </button>
        </>
      }
    >
      <form
        className="receive"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <ol className="receive-lines">
          {lines.map((l, i) => (
            <li key={l.key} className={'receive-line' + (l.mode === 'new' ? ' is-new' : '')}>
              {l.mode === 'existing' ? (
                <>
                  <label className="field grow">
                    <span>Item</span>
                    <select value={l.skuId} onChange={(e) => set(l.key, { skuId: e.target.value })} aria-label={`Item, line ${i + 1}`}>
                      <option value="">Choose an item…</option>
                      {sorted.map((s) => (
                        <option key={s.id} value={s.id} disabled={chosen.has(String(s.id)) && l.skuId !== String(s.id)}>
                          {s.productName}
                          {s.name ? ` ${s.name}` : ''} · {s.code} ({s.onHand} on shelf{s.short ? `, ${s.short} owed` : ''})
                        </option>
                      ))}
                    </select>
                  </label>
                  {l.skuId && byId.get(l.skuId)?.short ? (
                    <button type="button" className="chip-btn" onClick={() => set(l.key, { qty: String(byId.get(l.skuId)!.short) })}>
                      Fill the {byId.get(l.skuId)!.short} owed
                    </button>
                  ) : null}
                </>
              ) : (
                <div className="new-item">
                  <span className="new-tag">New item</span>
                  <label className="field grow">
                    <span>Name</span>
                    <input value={l.name} onChange={(e) => set(l.key, { name: e.target.value })} aria-label={`New item name, line ${i + 1}`} placeholder="e.g. Holo slab" />
                  </label>
                  <label className="field">
                    <span>SKU code</span>
                    <input value={l.code} onChange={(e) => set(l.key, { code: e.target.value })} aria-label={`SKU code, line ${i + 1}`} placeholder="auto" size={10} />
                  </label>
                  <label className="field">
                    <span>Price ($)</span>
                    <input value={l.price} onChange={(e) => set(l.key, { price: e.target.value })} aria-label={`Price, line ${i + 1}`} inputMode="decimal" placeholder="0.00" size={7} />
                  </label>
                  <label className="field">
                    <span>Alert at</span>
                    <input value={l.lowAt} onChange={(e) => set(l.key, { lowAt: e.target.value })} aria-label={`Low-stock alert at, line ${i + 1}`} inputMode="numeric" placeholder="0" size={4} />
                  </label>
                </div>
              )}
              <label className="field qty">
                <span>Quantity</span>
                <input value={l.qty} onChange={(e) => set(l.key, { qty: e.target.value })} aria-label={`Quantity, line ${i + 1}`} inputMode="numeric" size={5} />
              </label>
              <button type="button" className="icon-btn" onClick={() => remove(l.key)} aria-label={`Remove line ${i + 1}`} disabled={lines.length === 1}>
                <Icon name="close" size={16} />
              </button>
            </li>
          ))}
        </ol>
        <div className="receive-add">
          <button type="button" className="btn small" onClick={() => setLines((ls) => [...ls, blank()])}>
            <Icon name="plus" size={14} /> Another item
          </button>
          <button type="button" className="btn small" onClick={() => setLines((ls) => [...ls, fresh()])}>
            <Icon name="plus" size={14} /> New item
          </button>
        </div>
        <label className="field">
          <span>Note (supplier, invoice, box)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" maxLength={200} />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" hidden />
      </form>
    </Sheet>
  )
}
