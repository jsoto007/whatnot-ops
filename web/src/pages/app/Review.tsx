import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api, apiGet, type Product, type ReviewItem } from '../../lib/api'

const REASON_LABEL: Record<string, string> = {
  unmatched_item: 'Item not recognised',
  insufficient_stock: 'Not enough stock',
  ambiguous_sale: 'Which sale?',
  low_confidence: 'Not sure',
  no_open_sale: 'No open sale to cancel',
  cancel_sale: 'AI: looks cancelled',
  payment_failed: 'AI: payment problem',
  missing_sale: 'AI: possible missed sale',
  duplicate_sale: 'AI: possible duplicate',
  other: 'AI: something to check',
}
const ACTION_LABEL: Record<string, string> = {
  assign_sku: 'Assign the suggested SKU',
  cancel_sale: 'Cancel the sale',
  cancel: 'Cancel the sale',
  payment_failed: 'Mark payment failed',
  classify_sale: 'Record it as a sale',
  dismiss: 'Ignore this line',
}

export function Review() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['review'], queryFn: () => apiGet<{ items: ReviewItem[] }>('/api/review') })
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const refresh = () => qc.invalidateQueries()
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the review queue.</p>
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s) => ({ ...s, productName: p.name })))
  return (
    <section>
      <h2>Review</h2>
      <p className="muted">{q.data.items.length} open</p>
      {q.data.items.length === 0 ? (
        <div className="empty">
          <b>Nothing waiting</b>
          The rules handled everything they saw.
        </div>
      ) : (
        q.data.items.map((it) => <Item key={it.id} item={it} skus={skus} onDone={refresh} />)
      )}
    </section>
  )
}

function Item({ item, skus, onDone }: { item: ReviewItem; skus: { id: number; code: string; name: string; productName: string; available: number }[]; onDone: () => Promise<unknown> }) {
  const [skuId, setSkuId] = useState('')
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const resolve = useMutation({
    mutationFn: (body: Record<string, unknown>) => api('POST', `/api/review/${item.id}/resolve`, body),
    onSuccess: onDone,
    onError: (e) => setError((e as Error).message),
  })
  return (
    <div className="card review" data-testid={`review-${item.id}`}>
      <div className="product-head">
        <h3>
          {REASON_LABEL[item.reason] ?? item.reason}
          <span className="muted small"> · {item.source}</span>
        </h3>
        {item.showId && (
          <Link to={`/app/shows/${item.showId}`} className="small">
            {item.showDate} show
          </Link>
        )}
      </div>
      <p>{item.summary}</p>
      {!!item.proposal?.action && item.proposal.action !== 'none' && (
        <p className="proposal" data-testid="proposal">
          <b>AI suggests:</b> {ACTION_LABEL[String(item.proposal.action)] ?? String(item.proposal.action)}
          {typeof item.proposal.confidence === 'number' && <span className="muted"> · {Math.round(Number(item.proposal.confidence) * 100)}% sure</span>}
          {item.proposal.reasoning ? <span className="muted small"> — {String(item.proposal.reasoning)}</span> : null}
          {item.proposal.summary ? <span className="muted small"> — {String(item.proposal.summary)}</span> : null}
        </p>
      )}
      {item.context.length > 0 && (
        <div className="chat small">
          {item.context.map((c) => (
            <div className={'chat-line' + (item.message && c.seq === item.message.seq ? ' hit' : '')} key={c.seq}>
              <span className="t">#{c.seq}</span>
              <span className="u">{c.username}</span>
              <span>{c.body}</span>
            </div>
          ))}
        </div>
      )}
      <div className="inline-form">
        {(item.reason === 'unmatched_item' || item.reason === 'insufficient_stock') && (
          <>
            <label className="field">
              <span>SKU</span>
              <select value={skuId} onChange={(e) => setSkuId(e.target.value)} aria-label={`SKU for review ${item.id}`}>
                <option value="">Choose…</option>
                {skus.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.productName} {s.name} ({s.code}) · {s.available} available
                  </option>
                ))}
              </select>
            </label>
            {item.reason === 'unmatched_item' && (
              <label className="field check">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> <span>remember this name</span>
              </label>
            )}
            <button className="btn primary" disabled={!skuId || resolve.isPending} onClick={() => resolve.mutate({ action: 'assign_sku', skuId: Number(skuId), remember })}>
              Use this SKU
            </button>
          </>
        )}
        {item.reason === 'ambiguous_sale' && item.proposal && Array.isArray(item.proposal.candidates) && (
          <>
            {(item.proposal.candidates as { saleId: number; label: string }[]).map((c) => (
              <button key={c.saleId} className="btn" onClick={() => resolve.mutate({ action: 'pick_sale', saleId: c.saleId })}>
                {c.label}
              </button>
            ))}
          </>
        )}
        {!!item.proposal?.action && item.proposal.action !== 'none' && item.proposal.action !== 'dismiss' && (
          <button className="btn primary" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'apply_proposal' })} data-testid="apply-proposal">
            Apply suggestion
          </button>
        )}
        <button className="btn" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'dismiss' })}>
          Ignore
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  )
}
