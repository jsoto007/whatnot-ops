import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { Icon } from '../../components/Icon'
import { SkuPicker } from '../../components/SkuPicker'
import { api, apiGet, type Product, type ReviewItem, type Sku } from '../../lib/api'
import { fmtLongDate, plural } from '../../lib/format'
import { reviewSourceLabel } from '../../lib/labels'
import { groupByShow, type ShowGroup } from '../../lib/reviewGroups'

/** A SKU as the picker lists it: with its product's name (the chat names come along from /api/products). */
type ReviewSku = Sku & { productName: string }

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
  decline_seen: 'Card declined: waiting for Whatnot',
  lot_won_twice: 'One lot, two winners',
  order_declined: 'Payment declined: nothing recorded',
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
  chatter: 'Ignore this line',
  gift: 'Record it as a gift',
  missing_sale: 'Add the missed sale',
  duplicate_sale: 'Remove the duplicate',
  other: 'Take a look',
}

const showTitle = (g: { showDate: string | null }) => (g.showDate ? `${fmtLongDate(g.showDate)} show` : 'Not tied to a show')

/** `/app/review` lists the shows with decisions waiting; `/app/review/:showId` is one show's queue. */
export function Review() {
  const { showId } = useParams()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['review'], queryFn: () => apiGet<{ items: ReviewItem[] }>('/api/review') })
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const refresh = () => qc.invalidateQueries()
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the review queue.</p>
  const groups = groupByShow(q.data.items)
  if (!showId) return <ShowList groups={groups} total={q.data.items.length} />
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s) => ({ ...s, productName: p.name })))
  const group = groups.find((g) => g.key === showId)
  const elsewhere = q.data.items.length - (group?.items.length ?? 0)
  return <ShowQueue key={showId} group={group} elsewhere={elsewhere} skus={skus} onDone={refresh} />
}

/** One row per show with decisions waiting, newest show first. */
function ShowList({ groups, total }: { groups: ShowGroup[]; total: number }) {
  const shows = groups.filter((g) => g.showId != null).length
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Review</h2>
          <div className="sub">{total ? `${plural(total, 'decision', 'decisions')} waiting${shows ? ` across ${plural(shows, 'show', 'shows')}` : ''}. Open a show to work through its queue.` : 'Nothing waiting on you.'}</div>
        </div>
      </div>
      {total === 0 ? (
        <div className="empty">
          <b>Nothing waiting</b>
          The rules handled everything they saw.
        </div>
      ) : (
        <div className="card flush review-shows">
          <ul className="attention" data-testid="review-shows">
            {groups.map((g) => (
              <li key={g.key}>
                <Link to={`/app/review/${g.key}`} className="attn-row" data-testid={`review-show-${g.key}`}>
                  <span className="attn-icon tone-amber" aria-hidden>
                    <Icon name="review" size={20} />
                  </span>
                  <span className="attn-text">
                    <b>{showTitle(g)}</b>
                    <span>{[...g.byReason.entries()].map(([r, n]) => `${REASON_LABEL[r] ?? 'Something to check'} (${n})`).join(' · ')}</span>
                  </span>
                  <span className="attn-verb btn primary">{plural(g.items.length, 'decision', 'decisions')}</span>
                  <Icon name="chevron" className="attn-chev" size={18} />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

/** One show's decisions, filterable by reason. `group` is undefined once its queue is clear. */
function ShowQueue({ group, elsewhere, skus, onDone }: { group: ShowGroup | undefined; elsewhere: number; skus: ReviewSku[]; onDone: () => Promise<unknown> }) {
  const [reason, setReason] = useState('')
  const items = group?.items ?? []
  const visible = reason ? items.filter((it) => it.reason === reason) : items
  return (
    <section>
      <p className="small">
        <Link to="/app/review">← All shows</Link>
      </p>
      <div className="page-head">
        <div>
          <h2>{group ? showTitle(group) : 'Review'}</h2>
          <div className="sub">{items.length ? `${plural(items.length, 'decision', 'decisions')} waiting. Each one is a chat line the rules could not settle on their own.` : 'Nothing waiting for this show.'}</div>
        </div>
        {group?.showId != null && (
          <Link className="btn ghost" to={`/app/shows/${group.showId}`}>
            Open the show
          </Link>
        )}
      </div>
      {items.length > 0 && (
        <div className="chips filter" role="group" aria-label="Filter by reason" style={{ marginBottom: 'var(--space-3)' }}>
          <button className={'chip' + (reason === '' ? ' on' : '')} onClick={() => setReason('')}>
            All <span className="chip-n">{items.length}</span>
          </button>
          {[...group!.byReason.entries()].map(([r, n]) => (
            <button key={r} className={'chip' + (reason === r ? ' on' : '')} onClick={() => setReason(r)}>
              {REASON_LABEL[r] ?? 'Something to check'} <span className="chip-n">{n}</span>
            </button>
          ))}
        </div>
      )}
      {items.length === 0 ? (
        <div className="empty">
          <b>Nothing waiting</b>
          This show's queue is clear.
          {elsewhere > 0 && (
            <>
              {' '}
              <Link to="/app/review">{plural(elsewhere, 'decision is', 'decisions are')} waiting in other shows.</Link>
            </>
          )}
        </div>
      ) : (
        // A shelf suggestion that arrives later starts the item afresh (its picker and "remember").
        visible.map((it) => <Item key={`${it.id}:${it.shelf?.skuId ?? ''}`} item={it} skus={skus} onDone={onDone} />)
      )}
    </section>
  )
}

function Item({ item, skus, onDone }: { item: ReviewItem; skus: ReviewSku[]; onDone: () => Promise<unknown> }) {
  // The spot the winner typed is a choice for this win, not a name for the product: not remembered.
  const [skuId, setSkuId] = useState(item.shelf ? String(item.shelf.skuId) : '')
  // A title that names one lot of one show ("… #520") is never learned: the box is not offered.
  const canRemember = item.sale?.rememberName !== false
  // In a winner's-pick show the product chosen here is that winner's pick, not the lot's name:
  // remembering it is the person's choice, never the default.
  const [remember, setRemember] = useState(!item.shelf && canRemember && item.sale?.saleMode !== 'winners_pick')
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
          <b>AI suggests:</b> {ACTION_LABEL[String(item.proposal.action)] ?? 'Take a look'}
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
            {item.shelf && (
              <p className="small" data-testid="shelf-pick">
                Typed <b>{item.shelf.spot}</b> in chat: {item.shelf.name} on this show’s shelves.
              </p>
            )}
            <div className="sku-field">
              <span className="sku-label">SKU</span>
              <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`SKU for review ${item.id}`} />
            </div>
            {item.reason === 'unmatched_item' && canRemember && (
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
        {(item.reason === 'possible_duplicate' || item.reason === 'lot_won_twice') && item.sale && (
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
        {/* Also: the stream showed the card declined and Whatnot's order has not said yet. The
            order settles it by itself; this closes the sale now, for someone who is sure. */}
        {((item.reason === 'no_payment_record' && item.sale?.status === 'paid') || (item.reason === 'decline_seen' && !!item.sale && ['paid', 'reserved', 'detected'].includes(item.sale.status))) && (
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
              <div className="sku-field">
                <span className="sku-label">SKU</span>
                <SkuPicker skus={skus} value={skuId} onChange={setSkuId} label={`SKU for review ${item.id}`} showAvailable={false} />
              </div>
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
          {item.reason === 'payment_declined' ? 'Keep declined' : item.reason === 'decline_seen' || item.reason === 'lot_won_twice' ? 'Leave it to Whatnot' : item.reason === 'order_declined' ? 'Got it' : 'Ignore'}
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  )
}
