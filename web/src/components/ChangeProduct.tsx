import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from './Modal'
import { SkuPicker } from './SkuPicker'
import { ApiError, api, apiGet, money, type Product, type Sale, type Sku } from '../lib/api'
import { fmtDate } from '../lib/format'
import { lotText } from '../lib/labels'

/**
 * Give a sale that stands another product. The old one goes back to stock and the new one
 * leaves it; the price the buyer paid stays. Once the order is being picked nothing is put
 * back by itself: the answer says so, and Review gets a note for the packer.
 */
export function ChangeProduct({ sale, buyer, showDate, onClose, onDone }: { sale: Sale; buyer: string; showDate: string; onClose: () => void; onDone: (msg: string) => void | Promise<void> }) {
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s): Sku & { productName: string } => ({ ...s, productName: p.name })))
  const [skuId, setSkuId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const now = sale.sku ? `${sale.sku.productName} ${sale.sku.name}`.trim() : sale.item
  const chosen = skus.find((s) => String(s.id) === skuId)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api<{ changed: boolean; picked: boolean }>('PATCH', `/api/orders/sales/${sale.id}/product`, { skuId: Number(skuId) })
      const to = chosen ? `${chosen.productName} ${chosen.name}`.trim() : 'the new product'
      await onDone(r.picked ? `${buyer}'s ${lotText(sale)} is now ${to}. Picking had begun: swap it in the package and count the ${now} back in.` : `${buyer}'s ${lotText(sale)} is now ${to}. The ${now} is back in stock.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not change the product. Try again.')
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Change the product"
      sub={
        <>
          <b>{buyer}</b> · {lotText(sale)} · {fmtDate(showDate)}
          {sale.priceCents != null && ` · ${money(sale.priceCents)}`}
        </>
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || !skuId || Number(skuId) === sale.sku?.id} onClick={save} data-testid="change-product-save">
            Change the product
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        Now: <b>{now}</b>. The price the buyer paid does not change.
      </p>
      <div className="sku-field">
        <span className="sku-label">New product</span>
        <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`New product for ${buyer}'s ${lotText(sale)}`} />
      </div>
      {error && <p className="form-error">{error}</p>}
    </Modal>
  )
}
