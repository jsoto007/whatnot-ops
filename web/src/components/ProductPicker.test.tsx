import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { Product, Sale, Sku } from '../lib/api'
import { ProductPicker } from './ProductPicker'

const sku = (id: number, productId: number, code: string): Sku => ({ id, productId, code, name: '', priceCents: 100, weightOz: null, onHand: 9, reserved: 0, available: 9, short: 0, lowStockAt: 0, low: false, active: true, aliases: [] })
const products: Product[] = [{ id: 1, name: 'Glow serum', notes: null, active: true, skus: [sku(7, 1, 'GLOW')] }]
// Only what the dialog reads. Buyers are made up.
const sale = (item: string, rememberName: boolean): Sale => ({ id: 5, showId: 3, item, itemTitle: item.replace(/ #\d+$/, ''), lot: rememberName ? null : 520, rememberName, quantity: 1, priceCents: 700 }) as Sale

function mount(s: Sale) {
  const posts: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify({ alsoResolved: 0 }), { status: 200 })
      }
      if (url === '/api/products') return new Response(JSON.stringify({ products }), { status: 200 })
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      return new Response('{}', { status: 404 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ProductPicker target={{ sale: s, buyer: 'buyer_one', showDate: '2026-11-07', reviewId: 11, similar: [] }} onClose={() => {}} onDone={() => {}} />
    </QueryClientProvider>,
  )
  return posts
}

async function useGlow() {
  const box = screen.getByRole('combobox')
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'glow' } })
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1))
  fireEvent.click(screen.getByRole('option'))
  fireEvent.click(screen.getByTestId('use-product'))
}

afterEach(() => vi.unstubAllGlobals())

test('a title that names a product is offered to be remembered, ticked', async () => {
  const posts = mount(sale('Mystery Pull Bag', true))
  expect(screen.getByRole('checkbox', { name: /Remember this name/ })).toBeChecked()
  await useGlow()
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ action: 'assign_sku', skuId: 7, remember: true, applyToSimilar: false })
})

test('a sale from an older page load, with nothing said either way, is offered as before', () => {
  const old = { ...sale('Mystery Pull Bag', true) }
  delete old.rememberName
  mount(old)
  expect(screen.getByRole('checkbox', { name: /Remember this name/ })).toBeChecked()
})

test('in a winner’s-pick show the box is offered but not ticked', async () => {
  const posts = mount({ ...sale('Mystery Pull Bag', true), saleMode: 'winners_pick' })
  expect(screen.getByRole('checkbox', { name: /Remember this name/ })).not.toBeChecked()
  await useGlow()
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ action: 'assign_sku', skuId: 7, remember: false, applyToSimilar: false })
})

test('a title that names one lot of one show is not offered, and nothing is remembered', async () => {
  const posts = mount(sale('$1 BEAUTY PULLS #520', false))
  expect(screen.queryByRole('checkbox', { name: /Remember this name/ })).toBeNull()
  await useGlow()
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ action: 'assign_sku', skuId: 7, remember: false, applyToSimilar: false })
})
