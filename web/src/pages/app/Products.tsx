import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Field, Form } from '../../components/Form'
import { api, apiGet, money, type Product, type Sku } from '../../lib/api'

export function Products() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['products'], queryFn: () => apiGet<{ products: Product[] }>('/api/products') })
  const refresh = () => qc.invalidateQueries({ queryKey: ['products'] })
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [price, setPrice] = useState('')
  const write = can('inventory.write')

  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load products.</p>
  return (
    <section>
      <h2>Products</h2>
      {write && (
        <Form
          className="inline-form"
          submitLabel="Add product"
          onSubmit={async () => {
            await api('POST', '/api/products', {
              name,
              skus: [{ code: code || undefined, priceCents: price ? Math.round(parseFloat(price) * 100) : null }],
            })
            setName('')
            setCode('')
            setPrice('')
            await refresh()
          }}
        >
          <Field label="Product name">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Red bundle" required />
          </Field>
          <Field label="SKU code (optional)">
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="RED-BUNDLE" />
          </Field>
          <Field label="Price (USD, optional)">
            <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" placeholder="25.00" />
          </Field>
        </Form>
      )}
      {q.data.products.length === 0 ? (
        <p className="card muted">No products yet. Add one above or import the inventory sheet on the Inventory page.</p>
      ) : (
        q.data.products.map((p) => <ProductCard key={p.id} product={p} write={write} onChange={refresh} />)
      )}
    </section>
  )
}

function ProductCard({ product, write, onChange }: { product: Product; write: boolean; onChange: () => Promise<void> }) {
  const [skuCode, setSkuCode] = useState('')
  const [skuName, setSkuName] = useState('')
  const [adding, setAdding] = useState(false)
  return (
    <div className="card product" data-testid={`product-${product.id}`}>
      <div className="product-head">
        <h3>{product.name}</h3>
        {write && (
          <button className="btn small" onClick={() => setAdding((v) => !v)}>
            {adding ? 'Cancel' : 'Add variant'}
          </button>
        )}
      </div>
      {adding && (
        <Form
          className="inline-form"
          submitLabel="Add SKU"
          onSubmit={async () => {
            await api('POST', `/api/products/${product.id}/skus`, { code: skuCode, name: skuName })
            setSkuCode('')
            setSkuName('')
            setAdding(false)
            await onChange()
          }}
        >
          <Field label="SKU code">
            <input value={skuCode} onChange={(e) => setSkuCode(e.target.value)} required />
          </Field>
          <Field label="Variant name">
            <input value={skuName} onChange={(e) => setSkuName(e.target.value)} placeholder="Large" />
          </Field>
        </Form>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Variant</th>
            <th>Price</th>
            <th className="num">Available</th>
            <th>Chat names (aliases)</th>
          </tr>
        </thead>
        <tbody>
          {product.skus.map((s) => (
            <SkuRow key={s.id} sku={s} write={write} onChange={onChange} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SkuRow({ sku, write, onChange }: { sku: Sku; write: boolean; onChange: () => Promise<void> }) {
  const [alias, setAlias] = useState('')
  const [error, setError] = useState<string | null>(null)
  const add = useMutation({
    mutationFn: () => api('POST', `/api/skus/${sku.id}/aliases`, { alias }),
    onSuccess: async () => {
      setAlias('')
      setError(null)
      await onChange()
    },
    onError: (e) => setError((e as Error).message),
  })
  const remove = useMutation({ mutationFn: (id: number) => api('DELETE', `/api/aliases/${id}`), onSuccess: onChange })
  return (
    <tr className={sku.active ? '' : 'disabled'}>
      <td>
        <code>{sku.code}</code>
      </td>
      <td>{sku.name || '—'}</td>
      <td>{money(sku.priceCents)}</td>
      <td className="num">
        {sku.available}
        {sku.low && <span className="badge warn">low</span>}
      </td>
      <td>
        <div className="chips">
          {sku.aliases.map((a) => (
            <span className="chip" key={a.id}>
              {a.alias}
              {write && (
                <button aria-label={`Remove alias ${a.alias}`} onClick={() => remove.mutate(a.id)}>
                  ×
                </button>
              )}
            </span>
          ))}
          {write && (
            <form
              className="chip-form"
              onSubmit={(e) => {
                e.preventDefault()
                if (alias.trim()) add.mutate()
              }}
            >
              <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="add name" aria-label={`New alias for ${sku.code}`} />
            </form>
          )}
        </div>
        {error && <div className="form-error small">{error}</div>}
      </td>
    </tr>
  )
}
