import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from './Modal'
import { SkuPicker } from './SkuPicker'
import { ApiError, api, apiGet, money, type Product, type Sale, type SaleProduct, type Sku } from '../lib/api'
import { fmtDate } from '../lib/format'
import { lotText } from '../lib/labels'

const nameOf = (s: { productName: string; name: string }) => `${s.productName} ${s.name}`.trim()

/**
 * Change one of a standing sale's products, or give the sale one more ("two for one"). The
 * old product goes back to stock and the new one leaves it; the price the buyer paid stays.
 * Once the order is being picked nothing is put back by itself: the answer says so, and
 * Review gets a note for the packer.
 */
export function ChangeProduct({
  sale,
  buyer,
  showDate,
  product,
  add = false,
  onClose,
  onDone,
}: {
  sale: Sale
  buyer: string
  showDate: string
  /** Which product to change, for a sale that holds several. */
  product?: SaleProduct
  /** Add a product instead of changing one. */
  add?: boolean
  onClose: () => void
  onDone: (msg: string) => void | Promise<void>
}) {
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s): Sku & { productName: string } => ({ ...s, productName: p.name })))
  const [skuId, setSkuId] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const now = product ? nameOf(product.sku) : sale.sku ? nameOf(sale.sku) : sale.item
  const nowId = product ? product.sku.id : sale.sku?.id
  const chosen = skus.find((s) => String(s.id) === skuId)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const to = chosen ? nameOf(chosen) : 'the product'
      if (add) {
        const r = await api<{ picked: boolean }>('POST', `/api/orders/sales/${sale.id}/products`, { skuId: Number(skuId), quantity })
        await onDone(r.picked ? `${quantity} × ${to} added to ${buyer}'s ${lotText(sale)}. Picking had begun: add it to the package.` : `${quantity} × ${to} added to ${buyer}'s ${lotText(sale)}.`)
      } else {
        const r = await api<{ picked: boolean }>('PATCH', `/api/orders/sales/${sale.id}/product`, { skuId: Number(skuId), ...(product?.rowId ? { rowId: product.rowId } : {}) })
        await onDone(r.picked ? `${buyer}'s ${lotText(sale)} is now ${to}. Picking had begun: swap it in the package and count the ${now} back in.` : `${buyer}'s ${lotText(sale)} is now ${to}. The ${now} is back in stock.`)
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
      setBusy(false)
    }
  }
  return (
    <Modal
      title={add ? 'Add a product' : 'Change the product'}
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
          <button type="button" className="btn primary" disabled={busy || !skuId || (!add && Number(skuId) === nowId)} onClick={save} data-testid="change-product-save">
            {add ? 'Add the product' : 'Change the product'}
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        {add ? (
          <>
            On it now: <b>{(sale.products ?? []).map((p) => nameOf(p.sku)).join(' + ') || now}</b>. The price the buyer paid does not change.
          </>
        ) : (
          <>
            Now: <b>{now}</b>. The price the buyer paid does not change.
          </>
        )}
      </p>
      <div className="sku-field">
        <span className="sku-label">{add ? 'Product to add' : 'New product'}</span>
        <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`${add ? 'Product to add to' : 'New product for'} ${buyer}'s ${lotText(sale)}`} />
      </div>
      {add && (
        <label className="field" style={{ maxWidth: 140 }}>
          <span>How many</span>
          <input type="number" min={1} max={99} value={quantity} onChange={(e) => setQuantity(Math.max(1, Math.min(99, Number(e.target.value) || 1)))} aria-label="How many" />
        </label>
      )}
      {error && <p className="form-error">{error}</p>}
    </Modal>
  )
}
