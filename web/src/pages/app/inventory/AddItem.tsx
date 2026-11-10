import { useState } from 'react'
import { Modal } from '../../../components/Modal'
import { WeightInput } from '../../../components/WeightInput'
import { ApiError, api, type Product, type ReceiveResult } from '../../../lib/api'
import { plural, requestId } from '../../../lib/format'
import { parseWeight, weightProblem, type WeightUnit } from '../../../lib/weight'

const toInt = (s: string) => (s.trim() === '' ? null : /^\s*\d+\s*$/.test(s) ? parseInt(s, 10) : NaN)
/** "12.50" → 1250 cents; blank → null; anything else → NaN. */
const toCents = (s: string) => (s.trim() === '' ? null : /^\s*\d+(\.\d{1,2})?\s*$/.test(s) ? Math.round(parseFloat(s) * 100) : NaN)
/** The code the server would make from the name, shown as the placeholder. */
const codeFrom = (name: string) => name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 24) || 'CODE'

/**
 * Add a brand-new item: the product, its SKU, what is on the shelf now and the
 * words buyers type for it. Stock on hand goes in as a receipt, so it is in the ledger.
 */
export function AddItem({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [price, setPrice] = useState('')
  const [qty, setQty] = useState('')
  const [lowAt, setLowAt] = useState('')
  const [weight, setWeight] = useState<{ text: string; unit: WeightUnit }>({ text: '', unit: 'oz' })
  const [aliases, setAliases] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [receiptId] = useState(requestId)

  const submit = async () => {
    setError(null)
    if (!name.trim()) return setError('Give the item a name.')
    const priceCents = toCents(price)
    if (Number.isNaN(priceCents)) return setError('Price looks wrong — use 12 or 12.50.')
    const n = toInt(qty) ?? 0
    if (Number.isNaN(n)) return setError('Quantity is a whole number, 0 or more.')
    const low = toInt(lowAt)
    if (Number.isNaN(low)) return setError('The low-stock mark is a whole number.')
    const weightOz = parseWeight(weight.text, weight.unit)
    const weightBad = weightProblem(weightOz)
    if (weightBad) return setError(weightBad)
    const words = [...new Set(aliases.split(',').map((a) => a.trim().toLowerCase()).filter(Boolean))]
    setBusy(true)
    try {
      let skuId: number
      if (n > 0) {
        const r = await api<ReceiveResult>('POST', '/api/inventory/receive', {
          receiptId,
          note: 'new item',
          lines: [{ new: { name: name.trim(), code: code.trim() || undefined, priceCents, weightOz, lowStockAt: low ?? undefined }, quantity: n }],
        })
        skuId = r.lines[0]!.skuId
      } else {
        const r = await api<{ product: Product }>('POST', '/api/products', { name: name.trim(), skus: [{ code: code.trim() || undefined, priceCents, weightOz }] })
        skuId = r.product.skus[0]!.id
        if (low != null) await api('PATCH', `/api/skus/${skuId}`, { lowStockAt: low })
      }
      const missed: string[] = []
      for (const alias of words) {
        try {
          await api('POST', `/api/skus/${skuId}/aliases`, { alias })
        } catch {
          missed.push(alias)
        }
      }
      onDone(
        `Added ${name.trim()}${n > 0 ? ` with ${plural(n, 'unit', 'units')} on the shelf` : ''}` +
          (missed.length ? ` · couldn’t add alias ${missed.join(', ')} (already used?)` : ''),
      )
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Try again.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Add new item"
      sub="Creates the product, its SKU and the first stock movement."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" onClick={submit} disabled={busy} data-testid="add-item-submit">
            {busy ? 'Adding…' : 'Add to inventory'}
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        style={{ display: 'contents' }}
      >
        <label className="field">
          Item name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Mystery box" maxLength={160} />
        </label>
        <div className="modal-grid">
          <label className="field">
            SKU code (optional)
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder={codeFrom(name)} maxLength={64} />
          </label>
          <label className="field">
            Price (USD)
            <input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="10.00" inputMode="decimal" />
          </label>
          <label className="field">
            Quantity on shelf
            <input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="0" inputMode="numeric" />
          </label>
          <label className="field">
            Low-stock alert at
            <input value={lowAt} onChange={(e) => setLowAt(e.target.value)} placeholder="3" inputMode="numeric" />
          </label>
          <div className="field">
            <label htmlFor="add-weight">Weight of one unit (optional)</label>
            <WeightInput
              id="add-weight"
              text={weight.text}
              unit={weight.unit}
              onText={(text) => setWeight((w) => ({ ...w, text }))}
              onUnit={(unit) => setWeight((w) => ({ ...w, unit }))}
              label="Weight of one unit (optional)"
            />
          </div>
        </div>
        <label className="field">
          Chat aliases (comma separated — what buyers type)
          <input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="mystery, box" />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  )
}
