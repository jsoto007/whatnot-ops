import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api, apiGet, type Product, type ReviewItem } from '../../lib/api'
import { reviewSourceLabel } from '../../lib/labels'

const REASON_LABEL: Record<string, string> = {
  unmatched_item: 'Item not recognised',
  insufficient_stock: 'Not enough stock',
  ambiguous_sale: 'Which sale?',
  low_confidence: 'Not sure',
  no_open_sale: 'No open sale to cancel',
  no_payment_record: 'Not in the payment file',
  unmatched_payment: 'Paid but no matching sale',
  cancel_sale: 'AI: looks cancelled',
  payment_failed: 'AI: payment problem',
  missing_sale: 'AI: possible missed sale',
  duplicate_sale: 'AI: possible duplicate',
  other: 'AI: something to check',
  possible_duplicate: 'Same win twice?',
  player_unreadable: 'Win not readable',
  price_mismatch: 'Price differs from the payment file',
  payment_declined: 'Payment declined',
  decline_untied: 'Declined payment: which sale?',
  closed_after_picking: 'Closed after picking',
}
const ACTION_LABEL: Record<string, string> = {
  assign_sku: 'Assign the suggested SKU',
  cancel_sale: 'Cancel the sale',
  cancel: 'Cancel the sale',
  payment_failed: 'Mark payment failed',
  classify_sale: 'Record it as a sale',
  confirm_sale: 'Record it as a sale',
  dismiss: 'Ignore this line',
}

export function Review() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['review'], queryFn: () => apiGet<{ items: ReviewItem[] }>('/api/review') })
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const [reason, setReason] = useState('')
  const refresh = () => qc.invalidateQueries()
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the review queue.</p>
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s) => ({ ...s, productName: p.name })))
  const items = q.data.items
  const byReason = new Map<string, number>()
  for (const it of items) byReason.set(it.reason, (byReason.get(it.reason) ?? 0) + 1)
  const visible = reason ? items.filter((it) => it.reason === reason) : items
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Review</h2>
          <div className="sub">{items.length ? `${items.length} decision${items.length === 1 ? '' : 's'} waiting. Each one is a chat line the rules could not settle on their own.` : 'Nothing waiting on you.'}</div>
        </div>
      </div>
      {items.length > 0 && (
        <div className="chips filter" role="group" aria-label="Filter by reason" style={{ marginBottom: 'var(--space-3)' }}>
          <button className={'chip' + (reason === '' ? ' on' : '')} onClick={() => setReason('')}>
            All <span className="chip-n">{items.length}</span>
          </button>
          {[...byReason.entries()].map(([r, n]) => (
            <button key={r} className={'chip' + (reason === r ? ' on' : '')} onClick={() => setReason(r)}>
              {REASON_LABEL[r] ?? 'Something to check'} <span className="chip-n">{n}</span>
            </button>
          ))}
        </div>
      )}
      {items.length === 0 ? (
        <div className="empty">
          <b>Nothing waiting</b>
          The rules handled everything they saw.
        </div>
      ) : (
        visible.map((it) => <Item key={it.id} item={it} skus={skus} onDone={refresh} />)
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
          {REASON_LABEL[item.reason] ?? 'Something to check'}
          <span className="muted small"> · {reviewSourceLabel(item.source)}</span>
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
          {item.context.filter((c) => !item.message || Math.abs(c.seq - item.message.seq) <= 2).map((c) => (
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
        {(item.reason === 'ambiguous_sale' || item.reason === 'decline_untied') && item.proposal && Array.isArray(item.proposal.candidates) && (
          <>
            {(item.proposal.candidates as { saleId: number; label: string }[]).map((c) => (
              <button key={c.saleId} className="btn" onClick={() => resolve.mutate({ action: 'pick_sale', saleId: c.saleId })}>
                {c.label}
              </button>
            ))}
          </>
        )}
        {item.reason === 'low_confidence' && (
          <button className="btn primary" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'confirm_sale' })} data-testid="confirm-sale">
            Record as a sale
          </button>
        )}
        {/* A chat line about a win the video player already recorded: cancel the extra sale,
            or record the line if it really is a separate sale. "Ignore" keeps things as they are. */}
        {item.reason === 'possible_duplicate' && item.sale && (
          <button className="btn primary" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'set_status', status: 'cancelled' })} data-testid="cancel-duplicate">
            Cancel this sale
          </button>
        )}
        {item.reason === 'possible_duplicate' && !item.sale && (
          <button className="btn" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'confirm_sale' })} data-testid="confirm-separate-sale">
            It is a separate sale
          </button>
        )}
        {/* Paid at the win, yet the Seller Hub file does not list it: maybe a decline nobody saw. */}
        {item.reason === 'no_payment_record' && item.sale?.status === 'paid' && (
          <button className="btn" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'set_status', status: 'payment_failed' })} data-testid="mark-declined">
            Payment was declined
          </button>
        )}
        {/* Whatnot lets a declined buyer pay another way until the show ends. "Keep declined"
            leaves the sale closed and the item in stock. */}
        {item.reason === 'payment_declined' && item.sale?.status === 'payment_failed' && (
          <>
            {/* The win's item was never recognised: say which product it was first. */}
            {!item.sale.sku && (
              <label className="field">
                <span>SKU</span>
                <select value={skuId} onChange={(e) => setSkuId(e.target.value)} aria-label={`SKU for review ${item.id}`}>
                  <option value="">Choose…</option>
                  {skus.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.productName} {s.name} ({s.code})
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              className="btn primary"
              disabled={resolve.isPending || (!item.sale.sku && !skuId)}
              onClick={() => resolve.mutate({ action: 'set_status', status: 'paid', ...(item.sale?.sku ? {} : { skuId: Number(skuId) }) })}
              data-testid="mark-paid-after-decline"
            >
              They paid after all
            </button>
          </>
        )}
        {!!item.proposal?.action && item.proposal.action !== 'none' && item.proposal.action !== 'dismiss' && (
          <button className="btn primary" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'apply_proposal' })} data-testid="apply-proposal">
            Apply suggestion
          </button>
        )}
        <button className="btn" disabled={resolve.isPending} onClick={() => resolve.mutate({ action: 'dismiss' })}>
          {item.reason === 'payment_declined' ? 'Keep declined' : 'Ignore'}
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  )
}
