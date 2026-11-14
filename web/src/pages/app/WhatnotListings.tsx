import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { SkuPicker } from '../../components/SkuPicker'
import { ApiError, api, apiGet, money, type Product, type WhatnotCatalog, type WhatnotListing } from '../../lib/api'
import { fmtWhen } from '../../lib/format'
import { listingLinkLabel } from '../../lib/labels'

type Filter = 'all' | 'undecided' | 'differs'
const FILTERS: { key: Filter; label: string; match: (l: WhatnotListing) => boolean }[] = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'undecided', label: 'Not linked yet', match: (l) => !l.sku && l.link !== 'off' },
  { key: 'differs', label: 'Counts differ', match: (l) => l.differs },
]

/**
 * The shop's Whatnot listings next to its own products: which listing is which product,
 * and Whatnot's count beside the shop's. Reading and linking change nothing on Whatnot.
 */
export function WhatnotListings() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const [linking, setLinking] = useState<number | null>(null)
  // When "Read from Whatnot now" was pressed: the read runs in the background, so the
  // page looks again every few seconds until it shows a read newer than the press.
  const [askedAt, setAskedAt] = useState<number | null>(null)
  // First read still pending: look every 5 s for a minute, then every 15 s, and give up after 5 minutes.
  const waitingSince = useRef(Date.now())
  const q = useQuery({
    queryKey: ['whatnot-catalog'],
    queryFn: () => apiGet<WhatnotCatalog>('/api/whatnot/catalog'),
    refetchInterval: (query) => {
      const d = query.state.data
      if (!d?.connected || d.notAllowed) return false
      if (!d.readAt) {
        const waited = Date.now() - waitingSince.current
        return waited < 60_000 ? 5_000 : waited < 300_000 ? 15_000 : false
      }
      return askedAt !== null && Date.parse(d.readAt) < askedAt && Date.now() - askedAt < 90_000 ? 3_000 : false
    },
  })
  const products = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products'), enabled: can('inventory.write') })
  const skus = (products.data?.products ?? []).flatMap((p) => p.skus.map((s) => ({ ...s, productName: p.name })))
  const done = (c: WhatnotCatalog) => {
    qc.setQueryData(['whatnot-catalog'], c)
    void qc.invalidateQueries({ queryKey: ['inventory'] })
    setLinking(null)
  }
  const read = useMutation({
    mutationFn: () => {
      setAskedAt(Date.now())
      return api<WhatnotCatalog>('POST', '/api/whatnot/catalog/read')
    },
    onSuccess: done,
  })
  const link = useMutation({
    mutationFn: ({ id, skuId }: { id: number; skuId: number | null }) => api<WhatnotCatalog>('PATCH', `/api/whatnot/catalog/${id}/link`, { skuId }),
    onSuccess: done,
  })
  const failed = read.error ?? link.error
  const error = failed instanceof ApiError ? failed.message : failed ? 'That did not work. Try again.' : null

  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the Whatnot listings.</p>
  const c = q.data
  const edit = can('inventory.write')
  const shown = c.listings.filter(FILTERS.find((f) => f.key === filter)!.match)

  return (
    <section className="whatnot-listings">
      <p>
        <Link to="/app/inventory">← Inventory</Link>
      </p>
      <div className="page-head">
        <div>
          <h2>Whatnot listings</h2>
          <div className="sub">What the shop has listed on Whatnot, and which product here each listing is. An order for a linked listing names its product by itself.</div>
        </div>
        {c.connected && edit && (
          <div className="head-actions">
            <button type="button" className="btn ghost" disabled={read.isPending} onClick={() => read.mutate()}>
              {read.isPending ? 'Reading…' : 'Read from Whatnot now'}
            </button>
          </div>
        )}
      </div>
      {!c.connected && (
        <div className="empty">
          <b>Not connected to Whatnot</b>
          The shop's owner connects it in Settings → Whatnot.
        </div>
      )}
      {c.connected && (
        <p className="muted small" data-testid="catalog-state">
          {c.notAllowed
            ? 'The access token may not read the shop’s Whatnot catalog. Ask your developer to add that permission.'
            : c.readAt
              ? `Read from Whatnot ${fmtWhen(c.readAt)}.`
              : 'Not read from Whatnot yet.'}{' '}
          The app only shows both counts side by side. It never changes anything on Whatnot: fix a difference there.
        </p>
      )}
      {error && <p className="form-error">{error}</p>}
      {c.connected && c.listings.length === 0 && !c.notAllowed && c.readAt && (
        <div className="empty">
          <b>No listings on Whatnot</b>
          Whatnot's inventory for this shop is empty.
        </div>
      )}
      {c.listings.length > 0 && (
        <>
          <div className="toolbar">
            <div className="chips filter" role="group" aria-label="Filter listings">
              {FILTERS.map((f) => (
                <button key={f.key} className={'chip' + (filter === f.key ? ' on' : '')} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
                  {f.label} <span className="chip-n">{c.listings.filter(f.match).length}</span>
                </button>
              ))}
            </div>
          </div>
          <table className="table" data-testid="listings">
            <thead>
              <tr>
                <th>Listing on Whatnot</th>
                <th className="num">On Whatnot</th>
                <th>Product here</th>
                <th className="num">Can sell</th>
                {edit && (
                  <th>
                    <span className="sr-only">Link</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {shown.map((l) => (
                <tr key={l.id} data-testid={`listing-${l.skuCode ?? l.title}`}>
                  <td>
                    {l.url ? (
                      <a href={l.url} target="_blank" rel="noreferrer">
                        {l.title || 'Untitled'}
                      </a>
                    ) : (
                      l.title || 'Untitled'
                    )}
                    <span className="buyer-aka">
                      {[l.options, l.skuCode ? `Whatnot SKU ${l.skuCode}` : '', l.priceCents != null ? money(l.priceCents) : ''].filter(Boolean).join(' · ')}
                    </span>
                  </td>
                  <td className="num">{l.onWhatnot ?? <span className="muted">—</span>}</td>
                  <td>
                    {l.sku ? (
                      <>
                        {l.sku.name} <code>{l.sku.code}</code>
                      </>
                    ) : (
                      <span className="muted">{l.link === 'off' ? 'Not one of ours' : 'Not linked yet'}</span>
                    )}
                    <span className="buyer-aka">
                      {l.sku ? listingLinkLabel(l.link) : ''}
                      {l.sku && l.listings > 1 ? ` · one of ${l.listings} listings for this product${l.offered != null ? `, ${l.offered} offered in all` : ''}` : ''}
                    </span>
                  </td>
                  <td className="num">
                    {l.sku ? l.sku.canSell : <span className="muted">—</span>}
                    {l.differs && <span className="badge warn">differs</span>}
                  </td>
                  {edit && (
                    <td>
                      {linking === l.id ? (
                        <span className="link-pick">
                          <SkuPicker skus={skus} value="" onChange={(id) => id && link.mutate({ id: l.id, skuId: Number(id) })} label={`Product for ${l.title}`} />
                          <button type="button" className="btn ghost small" onClick={() => setLinking(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <>
                          <button type="button" className="btn ghost small" disabled={link.isPending} onClick={() => setLinking(l.id)}>
                            {l.sku ? 'Change' : 'Link to a product'}
                          </button>{' '}
                          {(l.sku || l.link !== 'off') && (
                            <button type="button" className="btn ghost small" disabled={link.isPending} onClick={() => link.mutate({ id: l.id, skuId: null })}>
                              {l.sku ? 'Unlink' : 'Not one of ours'}
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
