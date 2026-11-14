import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { Product, ReviewItem, Sale, Sku } from '../../lib/api'
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
  weightOz: null,
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

test('a title that names one lot of one show is not offered to be remembered', async () => {
  // Only the fields the card reads: the server says the name cannot be learned.
  const lot = { id: 40, item: '$1 BEAUTY PULLS #520', itemTitle: '$1 BEAUTY PULLS', lot: 520, rememberName: false } as Sale
  const posts = mount([{ ...item, summary: 'buyer_one bought "$1 BEAUTY PULLS #520" — which product is that?', sale: lot }], '/app/review/3')
  const box = await screen.findByRole('combobox', { name: 'SKU for review 7' })
  expect(screen.queryByRole('checkbox', { name: /remember this name/ })).toBeNull()
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'chari' } })
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1))
  fireEvent.click(screen.getByRole('option'))
  fireEvent.click(screen.getByRole('button', { name: 'Use this SKU' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]!.body).toEqual({ action: 'assign_sku', skuId: 4, remember: false })
})

test('a title that names a product is still offered, ticked', async () => {
  const named = { id: 41, item: 'Mystery Pull Bag', itemTitle: 'Mystery Pull Bag', lot: null, rememberName: true } as Sale
  mount([{ ...item, sale: named }], '/app/review/3')
  await screen.findByRole('combobox', { name: 'SKU for review 7' })
  expect(screen.getByRole('checkbox', { name: /remember this name/ })).toBeChecked()
})

test('in a winner’s-pick show the box is offered but not ticked: the product is that winner’s pick', async () => {
  const named = { id: 42, item: 'Mystery Pull Bag', itemTitle: 'Mystery Pull Bag', lot: null, rememberName: true, saleMode: 'winners_pick' } as Sale
  const posts = mount([{ ...item, sale: named }], '/app/review/3')
  const box = await screen.findByRole('combobox', { name: 'SKU for review 7' })
  const remember = screen.getByRole('checkbox', { name: /remember this name/ })
  expect(remember).not.toBeChecked()
  // A person may still say this lot is always that product.
  fireEvent.click(remember)
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'chari' } })
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1))
  fireEvent.click(screen.getByRole('option'))
  fireEvent.click(screen.getByRole('button', { name: 'Use this SKU' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]!.body).toEqual({ action: 'assign_sku', skuId: 4, remember: true })
})

test('a winner who typed a shelf spot gets that product suggested, not remembered as a name', async () => {
  const posts = mount([{ ...item, shelf: { spot: 'B4', skuId: 4, name: 'Holo slab Charizard' } }], '/app/review/3')
  expect(await screen.findByTestId('shelf-pick')).toHaveTextContent('Typed B4 in chat: Holo slab Charizard')
  expect(screen.getByRole('checkbox', { name: /remember this name/ })).not.toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: 'Use this SKU' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]!.body).toEqual({ action: 'assign_sku', skuId: 4, remember: false })
})

test('a card the stream showed declined is flagged: leave it to Whatnot, or close the sale if sure', async () => {
  const standing = { id: 50, item: 'Red bundle', status: 'paid', sku: { id: 1, code: 'RED', name: '', productName: 'Red bundle' } } as Sale
  const flag = { ...item, reason: 'decline_seen', summary: "Whatnot showed made_up_ann's card declined for 1 × Red bundle ($25.00).", sale: standing }
  const posts = mount([flag, { ...item, id: 8, reason: 'order_declined', summary: "Whatnot declined made_up_bo's payment for 1 × Lot 4 ($11.00).", sale: null }], '/app/review/3')
  const card = await screen.findByTestId('review-7')
  expect(card).toHaveTextContent('Card declined: waiting for Whatnot')
  expect(card).not.toHaveTextContent('decline_seen')
  fireEvent.click(screen.getByRole('button', { name: 'Leave it to Whatnot' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/7/resolve', body: { action: 'dismiss' } })
  fireEvent.click(screen.getByTestId('mark-declined'))
  await waitFor(() => expect(posts).toHaveLength(2))
  expect(posts[1]!.body).toEqual({ action: 'set_status', status: 'payment_failed' })
  // A payment Whatnot declined with no sale here: nothing to do but know of it.
  const other = screen.getByTestId('review-8')
  expect(other).toHaveTextContent('Payment declined: nothing recorded')
  expect(other.querySelectorAll('button')).toHaveLength(1)
  expect(other.querySelector('button')).toHaveTextContent('Got it')
})

test('one lot shown won by two buyers: cancel this sale, or leave it to Whatnot', async () => {
  const doubted = { id: 60, item: 'Red bundle #41', status: 'paid', sku: { id: 1, code: 'RED', name: '', productName: 'Red bundle' } } as Sale
  const posts = mount([{ ...item, reason: 'lot_won_twice', summary: 'Red bundle #41 was won by made_up_ann and then by made_up_bo.', sale: doubted }], '/app/review/3')
  const card = await screen.findByTestId('review-7')
  expect(card).toHaveTextContent('One lot, two winners')
  expect(card).not.toHaveTextContent('lot_won_twice')
  expect(screen.getByRole('button', { name: 'Leave it to Whatnot' })).toBeInTheDocument()
  fireEvent.click(screen.getByTestId('cancel-duplicate'))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/7/resolve', body: { action: 'set_status', status: 'cancelled' } })
})
