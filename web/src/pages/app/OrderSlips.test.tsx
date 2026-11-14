import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import type { OrderSlips as Doc, SlipRow } from '../../lib/api'
import { OrderSlips } from './OrderSlips'

const auth = (permissions: string[]): AuthState => ({
  me: {
    user: { id: 1, email: 'f@x.test', name: 'Fran', role: 'fulfillment', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: 'host_shop' },
    permissions,
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => permissions.includes(p),
  setMe: () => {},
  logout: async () => {},
})

const row = (over: Partial<SlipRow>): SlipRow => ({
  kind: 'sale',
  saleId: 1,
  auction: 'Quick Hit 2',
  product: '4 In 1 Video Making Kit',
  matched: true,
  giftCount: 0,
  priceCents: 1489,
  continued: false,
  whatnotOrder: null,
  inApp: true,
  onWhatnot: true,
  said: [],
  saidMore: 0,
  ...over,
})

const doc: Doc = {
  showId: 3,
  showDate: '2026-10-31',
  totals: { buyers: 2, items: 4, gifts: 2 },
  summary: [
    { username: 'Jessica88aguirre', items: 3, gifts: 2 },
    { username: 'Tinalou13699', items: 1, gifts: 0 },
  ],
  slips: [
    {
      username: 'Jessica88aguirre',
      orderId: 7,
      orderStatus: 'open',
      needsReview: true,
      items: 3,
      gifts: 2,
      rows: [
        row({ saleId: 1, giftCount: 2, priceCents: 3644, auction: 'Quick Hit 127', product: 'Electric Neck Fan With Mist' }),
        row({ saleId: 2, auction: 'Ultra 20', product: 'No Pick', matched: false, priceCents: 3765 }),
        row({ saleId: 2, auction: 'Ultra 20', product: 'No Pick', matched: false, priceCents: null, continued: true }),
      ],
      whatnot: null,
    },
    {
      username: 'Tinalou13699',
      orderId: 8,
      orderStatus: 'paid',
      needsReview: false,
      items: 1,
      gifts: 0,
      rows: [row({ saleId: 3, auction: 'Quick Hit 1', priceCents: 1670 })],
      whatnot: null,
    },
  ],
  whatnot: null,
}

function mount(permissions: string[], d: Doc = doc) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/order-slips')) return new Response(JSON.stringify(d), { status: 200 })
    if (url.endsWith('/api/auth/csrf')) return new Response(JSON.stringify({ token: 't' }), { status: 200 })
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ saleId: 3, giftCount: 1 }), { status: 200 })
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetch)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={['/app/orders/slips/3']}>
          <Routes>
            <Route path="/app/orders/slips/:showId" element={<OrderSlips />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return fetch
}

test('the cover and each buyer slip read like the shop sheet', async () => {
  mount(['orders.view'])
  const cover = await screen.findByTestId('slips-cover')
  expect(cover).toHaveTextContent('Auction Date: 10-31-2026')
  expect(screen.getByTestId('slips-totals')).toHaveTextContent('Total Buyers: 2Total Items: 4Total Gifts: 2')

  const jess = screen.getByTestId('slip-Jessica88aguirre')
  expect(within(jess).getByRole('heading')).toHaveTextContent('Username: Jessica88aguirre - 3 Items + 2 Gifts')
  const rows = within(jess).getAllByTestId('slip-row')
  expect(rows[0]).toHaveTextContent('10-31-2026Quick Hit 127Jessica88aguirreElectric Neck Fan With Mist Gift 2$36.44')
  expect(rows[0]).toHaveClass('has-gift')
  expect(rows[1]).toHaveTextContent('$37.65')
  expect(rows[2]!.lastElementChild).toHaveTextContent(/^-$/) // the lot's second pick: price is on the row above
  expect(within(jess).getByText('Ready To Ship', { exact: false })).toBeInTheDocument()
  expect(screen.getByTestId('slip-Tinalou13699')).toHaveTextContent('Username: Tinalou13699 - 1 Item')
  expect(screen.getByTestId('slips-review')).toHaveTextContent('1 buyer still has an item nobody has picked')
  // a viewer sees the gift count but cannot change it
  expect(screen.queryByRole('combobox')).toBeNull()
})

test('fulfilment sets how many gifts go in with a lot', async () => {
  const fetch = mount(['orders.view', 'orders.fulfill'])
  const tina = await screen.findByTestId('slip-Tinalou13699')
  fireEvent.change(within(tina).getByRole('combobox', { name: 'Gifts with Quick Hit 1' }), { target: { value: '1' } })
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/orders/sales/3/gifts', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ giftCount: 1 }) })))
  // the continued row of a two-pick lot has no control of its own
  expect(within(screen.getByTestId('slip-Jessica88aguirre')).getAllByRole('combobox')).toHaveLength(2)
})

test("Whatnot's shipment list is checked against the slips, on screen only", async () => {
  mount(['orders.view'])
  const check = await screen.findByTestId('whatnot-check')
  expect(check).toHaveTextContent('has not been read for this show')
  expect(check).toHaveTextContent('Send shipments to Sales Ops') // not connected: the laptop sends them
})

test('a shop connected to Whatnot is not sent to the laptop: the app reads the parcels itself', async () => {
  mount(['orders.view'], { ...doc, parcelsFromWhatnot: true })
  const check = await screen.findByTestId('whatnot-check')
  expect(check).toHaveTextContent('Sales Ops reads them from Whatnot by itself once the show has ended')
  expect(check).not.toHaveTextContent('Send shipments')
  expect(check).not.toHaveTextContent('Chat Scraper')
})

test('differences with Whatnot are spelled out', async () => {
  const [jess, tina] = doc.slips
  mount(['orders.view'], {
    ...doc,
    slips: [{ ...jess!, whatnot: { items: 4, statuses: ['Unfulfilled'] } }, { ...tina!, whatnot: { items: 1, statuses: ['Shipped'] } }],
    whatnot: { readAt: '2026-11-01T02:00:00Z', shipments: 3, lots: 0, lotsNotInApp: 0, salesNotOnWhatnot: 0, missingHere: ['nolan_k'], notOnWhatnot: [] },
  })
  const check = await screen.findByTestId('whatnot-check')
  expect(check.closest('.no-print')).not.toBeNull() // never printed
  expect(within(check).getByTestId('missing-here')).toHaveTextContent('nolan_k')
  expect(within(check).getByTestId('count-differs')).toHaveTextContent('Jessica88aguirre (here 3, Whatnot 4)')
  expect(within(screen.getByTestId('slip-Jessica88aguirre')).getByTestId('slip-whatnot')).toHaveTextContent('Whatnot: 4 items (Unfulfilled) differs')
  expect(within(screen.getByTestId('slip-Tinalou13699')).getByTestId('slip-whatnot')).toHaveTextContent('Whatnot: 1 item (Shipped)')
})

test("with Whatnot's lots read, each row shows its order number, its flags and what the winner said", async () => {
  const [jess] = doc.slips
  mount(['orders.view'], {
    ...doc,
    slips: [
      {
        ...jess!,
        rows: [
          row({ saleId: 1, auction: 'Listing 73', product: 'No Pick', matched: false, priceCents: 1700, whatnotOrder: '1462657228', said: ['can I get the Fitness Ball'] }),
          row({ saleId: null, auction: 'Listing 79', product: 'No Pick', matched: false, priceCents: 1500, whatnotOrder: '1462660717', inApp: false }),
          row({ saleId: 2, auction: 'Quick Hit 3', priceCents: 800, onWhatnot: false }),
        ],
      },
    ],
    whatnot: { readAt: '2026-11-01T02:00:00Z', shipments: 2, lots: 2, lotsNotInApp: 1, salesNotOnWhatnot: 1, missingHere: [], notOnWhatnot: [] },
  })
  const check = await screen.findByTestId('whatnot-check')
  expect(check).toHaveTextContent('Pick list from Whatnot · 2 lots in 2 shipments')
  expect(within(check).getByTestId('lots-not-in-app')).toHaveTextContent('1 lot on Whatnot with no win in the app')
  expect(within(check).getByTestId('sales-not-on-whatnot')).toHaveTextContent('1 win in the app that Whatnot does not list')
  const [first, second, third] = within(screen.getByTestId('slip-Jessica88aguirre')).getAllByTestId('slip-row')
  expect(first).toHaveTextContent('Order #1462657228')
  expect(within(first!).getByTestId('slip-said')).toHaveTextContent('Chat: “can I get the Fitness Ball”')
  expect(second).toHaveTextContent('not in app')
  expect(within(second!).queryByRole('combobox')).toBeNull() // no sale: no gift to set
  expect(third).toHaveTextContent('not on Whatnot')
})

test('a long chat shows its first line, how many were left out, and the last ones', async () => {
  const [jess] = doc.slips
  const said = ['congrats! what do you want?', 'the Fitness Ball please', 'lol', 'gl all']
  mount(['orders.view'], {
    ...doc,
    slips: [{ ...jess!, rows: [row({ product: 'No Pick', matched: false, said, saidMore: 2 })] }],
  })
  const [first] = await within(await screen.findByTestId('slip-Jessica88aguirre')).findAllByTestId('slip-row')
  expect(within(first!).getByTestId('slip-said')).toHaveTextContent(
    'Chat: “congrats! what do you want?” · … 2 more … · “the Fitness Ball please” · “lol” · “gl all”',
  )
})
