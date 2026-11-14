import { useContext, useState } from 'react'
import { AuthContext } from '../auth/context'
import { useQuery } from '@tanstack/react-query'
import { Modal } from './Modal'
import { SkuPicker } from './SkuPicker'
import { ApiError, api, apiGet, money, type Product, type Sale, type Sku } from '../lib/api'
import { fmtDate, plural } from '../lib/format'
import { lotText } from '../lib/labels'

type PickerSku = Sku & { productName: string }

/** The last few products picked on this laptop: one tap instead of a search when a show repeats them. */
const RECENT_KEY = 'wn.recentProducts'
const readRecent = (): number[] => {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((n): n is number => typeof n === 'number').slice(0, 4) : []
  } catch {
    return []
  }
}
const pushRecent = (id: number) => {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...readRecent().filter((n) => n !== id)].slice(0, 4)))
  } catch {
    /* private window or storage off: nothing to remember */
  }
}

/** The lot the product is for: one buyer's one win. */
export interface PickerTarget {
  sale: Sale
  buyer: string
  showDate: string
  /** The open "item not recognised" decision on that sale. */
  reviewId: number
  /** Other lots in the same show with the same title and no product yet (other orders too). */
  similar: Sale[]
  /** Start with "also use it for the other lots" ticked (the banner's "name it once"). */
  applyToSimilar?: boolean
}

/**
 * "Which product is “$1 BEAUTY PULLS #520”?" for one buyer's lot. The pick lands on that lot
 * alone unless the person ticks the box for the other lots of the show with the same title.
 */
export function ProductPicker({ target, onClose, onDone }: { target: PickerTarget; onClose: () => void; onDone: (msg: string) => void | Promise<void> }) {
  const { sale, buyer, showDate, reviewId, similar } = target
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const skus: PickerSku[] = (products.data?.products ?? []).flatMap((p) => p.skus.map((s) => ({ ...s, productName: p.name })))
  const [skuId, setSkuId] = useState('')
  // A title that names one lot of one show ("… #520") is never learned: the box is not offered.
  const canRemember = sale.rememberName !== false
  // In a winner's-pick show the product chosen here is that winner's pick, not the lot's name:
  // remembering it is the person's choice, never the default.
  // Where each listing is one product, the win's Whatnot listing can be linked to the product
  // chosen here, so its later orders name themselves. Not for a listing the team marked "not
  // one of ours", and only for people who may link listings.
  const auth = useContext(AuthContext)
  const canLink = sale.saleMode === 'listing' && !!sale.listing && !sale.listing.off && !sale.listing.linked && !!auth?.can('inventory.write')
  const [linkListing, setLinkListing] = useState(canLink)
  // The link is the exact way to remember a listing: the name is then not ticked by default.
  const [remember, setRemember] = useState(canRemember && sale.saleMode !== 'winners_pick' && !canLink)
  const [applyAll, setApplyAll] = useState(!!target.applyToSimilar && similar.length > 0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const chosen = skuId ? skus.find((s) => String(s.id) === skuId) : undefined
  const recent = readRecent()
    .map((id) => skus.find((s) => s.id === id))
    .filter((s): s is PickerSku => !!s && String(s.id) !== skuId)
  const title = sale.itemTitle || sale.item
  const units = sale.quantity + (applyAll ? similar.reduce((n, s) => n + s.quantity, 0) : 0)

  const submit = async () => {
    if (!chosen) return
    setBusy(true)
    setError(null)
    try {
      // The other lots are named by id: exactly the ones this dialog listed, never a lot that
      // appeared after it opened. The server re-checks each is still a similar open lot.
      const applyTo = applyAll ? similar.map((s) => s.review?.id).filter((id): id is number => id != null) : undefined
      const r = await api<{ alsoResolved: number; listingLinked?: boolean }>('POST', `/api/review/${reviewId}/resolve`, {
        action: 'assign_sku',
        skuId: chosen.id,
        remember,
        applyToSimilar: applyAll,
        ...(applyTo ? { applyTo } : {}),
        ...(canLink && linkListing ? { linkListing: true } : {}),
      })
      pushRecent(chosen.id)
      const also = r?.alsoResolved ?? 0
      const linked = r?.listingLinked ? ` The listing “${sale.listing?.title}” now always uses it.` : ''
      await onDone(`${chosen.productName} set for ${buyer}'s ${lotText(sale)}${also ? ` and ${plural(also, 'other lot', 'other lots')}` : ''}.${linked}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not set the product. Try again.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Which product is “${sale.item}”?`}
      sub={
        <>
          <b>{buyer}</b> · {lotText(sale)} · {fmtDate(showDate)}
          {sale.priceCents != null && ` · ${money(sale.priceCents)}`}
          {sale.quantity > 1 && ` · ${sale.quantity} units`}
        </>
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn primary" onClick={submit} disabled={!chosen || busy} data-testid="use-product">
            {applyAll ? `Use it for ${plural(1 + similar.length, 'lot', 'lots')}` : 'Use it for this lot'}
          </button>
        </>
      }
    >
      <div className="product-picker">
        <div className="sku-field">
          <span className="sku-label">Product</span>
          <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`Product for ${buyer}'s ${lotText(sale)}`} />
        </div>
        {recent.length > 0 && !chosen && (
          <div className="recent-row" aria-label="Recent products">
            <span className="small muted">Recent</span>
            {recent.map((s) => (
              <button key={s.id} type="button" className="chip-btn" onClick={() => setSkuId(String(s.id))}>
                {s.productName}
                {s.name ? ` ${s.name}` : ''}
              </button>
            ))}
          </div>
        )}
        {chosen && (
          <p className="small muted stock-note" data-testid="stock-note">
            <b>{chosen.available}</b> available · {plural(units, 'unit', 'units')} will be sold from it
            {chosen.available - units < 0 && (
              <>
                {' '}
                · <span className="warn-text">short by {units - chosen.available}</span>
              </>
            )}
          </p>
        )}
        {canLink && (
          <label className="field check">
            <input type="checkbox" checked={linkListing} onChange={(e) => setLinkListing(e.target.checked)} data-testid="link-listing" />
            <span>
              Always use this product for this listing{' '}
              <span className="muted">
                — “{sale.listing?.title}
                {sale.listing?.options ? ` · ${sale.listing.options}` : ''}” on Whatnot. Its other wins in this show are named now, and its later orders name themselves.
              </span>
            </span>
          </label>
        )}
        {canRemember && (
          <label className="field check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            <span>
              Remember this name <span className="muted">— next time the chat says “{sale.item}”, no review is needed.</span>
            </span>
          </label>
        )}
        {similar.length > 0 && (
          <label className="field check">
            <input type="checkbox" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} data-testid="apply-similar" />
            <span>
              Also use it for the {plural(similar.length, 'other lot', 'other lots')} titled “{title} #…” in this show{' '}
              <span className="muted">— their orders move to Ready to pick.</span>
            </span>
          </label>
        )}
        {error && <p className="form-error">{error}</p>}
      </div>
    </Modal>
  )
}
