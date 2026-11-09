import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from './Modal'
import { SkuPicker } from './SkuPicker'
import { ApiError, api, apiGet, money, type Product, type Sale, type Sku } from '../lib/api'
import { fmtDate } from '../lib/format'
import { lotText } from '../lib/labels'

/**
 * A card Whatnot declined after the win. The buyer may still pay another way before the show
 * ends: a person either restores the sale (saying which product it was, if nobody knew) or
 * keeps it declined, which leaves the item in stock.
 */
export function DeclineDialog({ sale, buyer, showDate, reviewId, onClose, onDone }: { sale: Sale; buyer: string; showDate: string; reviewId: number; onClose: () => void; onDone: (msg: string) => void | Promise<void> }) {
  const needsSku = !sale.sku
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products'), enabled: needsSku })
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s): Sku & { productName: string } => ({ ...s, productName: p.name })))
  const [skuId, setSkuId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const act = async (body: Record<string, unknown>, msg: string) => {
    setBusy(true)
    setError(null)
    try {
      await api('POST', `/api/review/${reviewId}/resolve`, body)
      await onDone(msg)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not update the sale. Try again.')
      setBusy(false)
    }
  }
  const item = sale.sku ? `${sale.sku.productName} ${sale.sku.name}`.trim() : sale.item
  return (
    <Modal
      title="Payment declined"
      sub={
        <>
          <b>{buyer}</b> · {lotText(sale)} · {fmtDate(showDate)}
          {sale.priceCents != null && ` · ${money(sale.priceCents)}`}
        </>
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" disabled={busy} onClick={() => act({ action: 'dismiss' }, `${buyer}'s ${lotText(sale)} stays declined.`)} data-testid="keep-declined">
            Keep declined
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={busy || (needsSku && !skuId)}
            onClick={() => act({ action: 'set_status', status: 'paid', ...(needsSku ? { skuId: Number(skuId) } : {}) }, `${buyer}'s ${lotText(sale)} is paid again and back on the order.`)}
            data-testid="paid-after-all"
          >
            They paid after all
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        Whatnot declined the card for <b>{item}</b>. {sale.flagged ? 'Picking had already begun, so the item was not put back in stock.' : 'The item is back in stock.'} The buyer can still pay another way until the show ends; if they did, restore the sale.
      </p>
      {needsSku && (
        <div className="sku-field">
          <span className="sku-label">Which product was it?</span>
          <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`Product for ${buyer}'s ${lotText(sale)}`} showAvailable={false} />
        </div>
      )}
      {error && <p className="form-error">{error}</p>}
    </Modal>
  )
}
