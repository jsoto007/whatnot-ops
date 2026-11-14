import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { Modal } from '../../components/Modal'
import { SkuPicker } from '../../components/SkuPicker'
import { ApiError, api, apiGet, type Giveaway, type Product, type Sku } from '../../lib/api'
import { fmtDate, plural } from '../../lib/format'

const nameOf = (s: { productName: string; name: string }) => `${s.productName} ${s.name}`.trim()

/**
 * "Giveaways to assign": the giveaways Whatnot lists for the shop's shows, each waiting for a
 * person to say which product it was. A giveaway is not a sale (no price, not in the sales
 * numbers); its product leaves the shelf when it is named and comes back when it is changed
 * or taken off.
 */
export function Giveaways() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['giveaways'], queryFn: () => apiGet<{ giveaways: Giveaway[]; waiting: number }>('/api/giveaways') })
  const [naming, setNaming] = useState<Giveaway | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])
  const canAct = can('review.act')
  const done = async (msg: string) => {
    await qc.invalidateQueries()
    setNaming(null)
    setToast(msg)
  }
  const takeOff = async (g: Giveaway) => {
    setError(null)
    try {
      await api('DELETE', `/api/giveaways/${g.id}/product`)
      await done(`${g.product ? nameOf(g.product) : 'The product'} is off ${g.buyer}'s giveaway and back in stock.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
    }
  }
  const rows = q.data?.giveaways ?? []
  const waiting = q.data?.waiting ?? 0
  return (
    <section>
      <p>
        <Link to="/app/orders">← Orders</Link>
      </p>
      <h2>Giveaways to assign</h2>
      <p className="muted small">
        Giveaways Whatnot lists for your shows. Say which product each one was: it leaves the shelf then, and prints on the buyer's slip. A giveaway is not a sale, so it is not in the sales numbers.
      </p>
      {error && <p className="form-error">{error}</p>}
      {q.data && rows.length === 0 && (
        <div className="empty">
          <b>No giveaways to assign</b>
          They appear here once Whatnot lists a giveaway for one of your shows.
        </div>
      )}
      {rows.length > 0 && (
        <>
          <p data-testid="giveaways-waiting">
            <b>{waiting === 0 ? 'Every giveaway has its product.' : `${plural(waiting, 'giveaway', 'giveaways')} waiting for a product.`}</b>
          </p>
          <table className="table" data-testid="giveaways">
            <thead>
              <tr>
                <th>Show</th>
                <th>Buyer</th>
                <th>Giveaway</th>
                <th className="num">Quantity</th>
                <th>Product</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.id} data-testid={`giveaway-${g.buyer}`}>
                  <td>
                    <Link to={`/app/orders/slips/${g.showId}`}>{fmtDate(g.showDate)}</Link>
                  </td>
                  <td>
                    <b>{g.buyer}</b>
                  </td>
                  <td>{g.title}</td>
                  <td className="num">{g.quantity}</td>
                  <td>
                    {g.product ? nameOf(g.product) : <span className="muted">Waiting for a product</span>}
                    {g.cancelled && <div className="muted small">Whatnot cancelled this giveaway: take the product off to put it back in stock.</div>}
                  </td>
                  <td>
                    {canAct && (
                      <div className="acts">
                        {!g.cancelled && (
                          <button type="button" className={'btn small' + (g.product ? '' : ' primary')} onClick={() => setNaming(g)}>
                            {g.product ? 'Change' : 'Name the product'}
                          </button>
                        )}
                        {g.product && (
                          <button type="button" className="btn small ghost" onClick={() => takeOff(g)}>
                            Take it off
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {naming && <NameGiveaway giveaway={naming} onClose={() => setNaming(null)} onDone={done} />}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

function NameGiveaway({ giveaway: g, onClose, onDone }: { giveaway: Giveaway; onClose: () => void; onDone: (msg: string) => void | Promise<void> }) {
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s): Sku & { productName: string } => ({ ...s, productName: p.name })))
  const [skuId, setSkuId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const chosen = skus.find((s) => String(s.id) === skuId)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api('PUT', `/api/giveaways/${g.id}/product`, { skuId: Number(skuId) })
      const to = chosen ? nameOf(chosen) : 'the product'
      await onDone(g.product ? `${g.buyer}'s giveaway is now ${to}. The ${nameOf(g.product)} is back in stock.` : `${g.buyer}'s giveaway is ${g.quantity} × ${to}. It is out of stock and on the slip.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
      setBusy(false)
    }
  }
  return (
    <Modal
      title={g.product ? "Change the giveaway's product" : "Name the giveaway's product"}
      sub={
        <>
          <b>{g.buyer}</b> · {g.title} · {fmtDate(g.showDate)}
        </>
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || !skuId || Number(skuId) === g.product?.id} onClick={save} data-testid="giveaway-save">
            {g.product ? 'Change the product' : 'Name the product'}
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        {g.product ? (
          <>
            Now: <b>{nameOf(g.product)}</b>. It goes back in stock and the new one leaves it.
          </>
        ) : (
          <>{g.quantity} × the product you choose leaves the shelf. Nothing is charged: a giveaway has no price.</>
        )}
      </p>
      <div className="sku-field">
        <span className="sku-label">Product</span>
        <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`Product for ${g.buyer}'s giveaway`} />
      </div>
      {error && <p className="form-error">{error}</p>}
    </Modal>
  )
}
