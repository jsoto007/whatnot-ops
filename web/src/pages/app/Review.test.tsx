import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { Product, ReviewItem, Sku } from '../../lib/api'
import { Review } from './Review'

const item: ReviewItem = {
  id: 7,
  showId: 3,
  showDate: '2026-10-24',
  reason: 'unmatched_item',
  summary: 'kat.c won "mystery box" for $12.00: no product by that name.',
  source: 'rules',
  status: 'open',
  proposal: null,
  message: null,
  context: [],
  sale: null,
  createdAt: '2026-10-25T01:38:06Z',
}
const sku = (id: number, productId: number, code: string, name: string, aliases: string[] = []): Sku => ({
  id,
  productId,
  code,
  name,
  priceCents: null,
  onHand: 3,
  reserved: 0,
  available: 3,
  short: 0,
  lowStockAt: 0,
  low: false,
  active: true,
  aliases: aliases.map((alias, i) => ({ id: i + 1, alias })),
})
const products: Product[] = [
  { id: 1, name: 'Red bundle', notes: null, active: true, skus: [sku(1, 1, 'RED', ''), sku(3, 1, 'RED-XL', 'XL')] },
  { id: 2, name: 'Holo slab', notes: null, active: true, skus: [sku(4, 2, 'HOLO', 'Charizard', ['charizard slab'])] },
]

function mount(items: ReviewItem[], path: string) {
  const posts: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/review') return new Response(JSON.stringify({ items }), { status: 200 })
      if (url === '/api/products') return new Response(JSON.stringify({ products }), { status: 200 })
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        return new Response('{}', { status: 200 })
      }
      return new Response('{}', { status: 404 })
    }),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/app/review" element={<Review />} />
          <Route path="/app/review/:showId" element={<Review />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return posts
}

test('the queue is listed by show, newest first, and a show opens only its own decisions', async () => {
  mount(
    [
      item,
      { ...item, id: 8, showId: 5, showDate: '2026-10-25', reason: 'low_confidence', summary: 'viewer_vic: "I will take it" — a sale?' },
      { ...item, id: 9, reason: 'possible_duplicate', summary: 'Same win twice?' },
    ],
    '/app/review',
  )
  const rows = await screen.findAllByTestId(/^review-show-/)
  expect(rows).toHaveLength(2)
  expect(rows[0]).toHaveTextContent(/Oct 25.*show/)
  expect(rows[0]).toHaveTextContent('Not sure (1)')
  expect(rows[0]).toHaveTextContent('1 decision')
  expect(rows[1]).toHaveTextContent(/Oct 24.*show/)
  expect(rows[1]).toHaveTextContent('Item not recognised (1) · Same win twice? (1)')
  expect(rows[1]).toHaveTextContent('2 decisions')
  expect(screen.queryByTestId('review-7')).toBeNull()

  fireEvent.click(rows[1])
  expect(await screen.findByTestId('review-7')).toBeInTheDocument()
  expect(screen.getByTestId('review-9')).toBeInTheDocument()
  expect(screen.queryByTestId('review-8')).toBeNull()
  expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/Oct 24.*show/)
  expect(screen.getByRole('link', { name: 'Open the show' })).toHaveAttribute('href', '/app/shows/3')
  fireEvent.click(screen.getByRole('link', { name: '← All shows' }))
  expect(await screen.findAllByTestId(/^review-show-/)).toHaveLength(2)
})

test('a show whose queue is clear says so and points at the other shows', async () => {
  mount([{ ...item, id: 8, showId: 5, showDate: '2026-10-25' }], '/app/review/3')
  expect(await screen.findByText('Nothing waiting', { exact: true })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '1 decision is waiting in other shows.' })).toHaveAttribute('href', '/app/review')
})

test('an unrecognised item is matched to a SKU by searching the catalogue, then assigned', async () => {
  const posts = mount([item], '/app/review/3')
  const box = await screen.findByRole('combobox', { name: 'SKU for review 7' })
  const use = screen.getByRole('button', { name: 'Use this SKU' })
  expect(use).toBeDisabled()
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'chari' } })
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1))
  expect(screen.getByRole('option')).toHaveTextContent('Holo slab Charizard')
  expect(screen.getByRole('option')).toHaveTextContent('HOLO · 3 available')
  fireEvent.click(screen.getByRole('option'))
  expect(box).toHaveValue('Holo slab · Charizard (HOLO)')
  expect(use).toBeEnabled()
  fireEvent.click(use)
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/7/resolve', body: { action: 'assign_sku', skuId: 4, remember: true } })
})
