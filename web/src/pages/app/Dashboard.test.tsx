import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Overview, ShowTiles, WhatnotMoney } from '../../lib/api'
import { Dashboard } from './Dashboard'

// Made-up shop: names and amounts are not anyone's.
const tiles = (showId: number, date: string, paid: number): ShowTiles => ({
  showId,
  date,
  startedAt: `${date}T07:00:00Z`,
  updatedAt: `${date}T10:00:00Z`,
  live: false,
  messages: 400,
  itemsSold: 9,
  buyers: 4,
  red: 0,
  bundles: 0,
  ultra: 0,
  mega: 0,
  quickHit: 0,
  gifts: 0,
  noPicks: 0,
  noWinner: 0,
  cancelled: 0,
  revenuePaidCents: paid,
  revenuePendingCents: 0,
  orders: {},
  toFulfil: 0,
  openReviews: 0,
})
const overview: Overview = {
  shows: [tiles(3, '2026-11-12', 500), tiles(2, '2026-11-12', 11100), tiles(1, '2026-11-05', 22200)],
  totals: { shows: 2, itemsSold: 18, buyers: 8, messages: 800, revenuePaidCents: 33300, revenuePendingCents: 0 },
  topBuyers: [{ buyer: 'chat_name', items: 9, paidCents: 11100, shows: 1 }],
  topSkus: [],
  lowStock: [],
  attention: { openReviews: 0, reviewReasons: {}, toFulfil: 0, fulfilItems: 0, fulfilOldestShow: null, lowStock: 0, oversold: [], unreconciledShows: [], live: null },
}
const whatnot = (days: number): WhatnotMoney => ({
  connected: true,
  days,
  currency: 'USD',
  ordersReadAt: new Date(Date.now() - 5 * 60_000).toISOString(),
  sales: {
    paidCents: days === 7 ? 1700 : 5045,
    orders: 3,
    items: 4,
    buyers: 2,
    averageOrderCents: 1682,
    pendingCents: 1000,
    pendingOrders: 1,
    cancelledCents: 500,
    cancelledOrders: 1,
    giveaways: 1,
    otherCurrencyOrders: 0,
  },
  customers: {
    new: { buyers: 1, orders: 2, paidCents: days === 7 ? 1700 : 4200 },
    returning: { buyers: 1, orders: 1, paidCents: days === 7 ? 0 : 845 },
    knownSince: '2026-10-04T23:00:00+00:00',
  },
  ledger: { currency: 'USD', otherCurrencyLines: 0, netCents: 1997, kinds: [], fees: {}, itemPricesCents: 2500, costOfGoodsCents: null, through: '2026-11-13T10:00:00+00:00' },
  feesCents: 303,
  ledgerStatus: { lines: 3, building: false, lastImportedAt: null, notAllowed: false, lastFailed: false },
  topBuyers: [
    { buyer: 'mika_r', paidCents: 4200, orders: 2, shows: 1, returning: false },
    { buyer: 'jo.b', paidCents: 845, orders: 1, shows: 1, returning: true },
  ],
  shows: [
    { showId: 2, paidCents: 4200, pendingCents: 1000, orders: 2, items: 3, buyers: 1, netCents: -525 },
    { showId: 3, sameAsShowId: 2 },
  ],
})

const auth = (permissions: string[]): AuthState => ({ me: null, loading: false, can: (p: string) => permissions.includes(p), refresh: async () => {}, logout: async () => {} }) as unknown as AuthState
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function page(permissions: string[], answer: (days: number) => unknown, status = 200) {
  const fetch = vi.fn(async (url: string) => {
    if (url.startsWith('/api/dashboard/whatnot')) return json(answer(Number(new URL(url, 'http://x').searchParams.get('days'))), status)
    if (url.startsWith('/api/buyers/flags')) return json({ flags: { mika_r: { flag: 'vip', note: false } } })
    return json(overview)
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return fetch
}

afterEach(() => vi.unstubAllGlobals())

test("connected to Whatnot, the money, the shows' sales and the top buyers are Whatnot's", async () => {
  const fetch = page(['shows.view', 'reports.view'], whatnot)
  const card = await screen.findByTestId('whatnot-money')
  expect(card.textContent).toContain('Sales on Whatnot · last 30 days')
  expect(card.textContent).toContain('$50.45')
  expect(card.textContent).toContain('3 paid orders · $16.82 per order')
  // Paid sales by customer; failed payments are not on the card at all.
  const split = screen.getByTestId('customers')
  expect(split.textContent).toContain('New customers $42.00 · 1 buyer')
  expect(split.textContent).toContain('Returning $8.45 · 1 buyer')
  expect(split.textContent).toContain('Returning: bought here before these 30 days, as far back as Whatnot’s orders in the app go (Oct 4).')
  expect(split.textContent).toContain('$10.00 more is still being paid.')
  expect(card.textContent).not.toMatch(/failed|Failed|declined/)
  expect(screen.getByTestId('tile-net').textContent).toContain('$19.97')
  expect(screen.getByTestId('tile-net').textContent).toContain('ledger lines of these 30 days, through Nov 13')
  expect(screen.getByTestId('tile-fees').textContent).toContain('$3.03')
  expect(screen.getByTestId('tile-buyers').textContent).toContain('1 new · 1 returning')
  expect(screen.getByTestId('tile-items').textContent).toContain('1 giveaway')
  expect(card.textContent).toContain('Orders read from Whatnot 5 min ago')
  // The app's own count of the same shows is not on the page.
  expect(document.body.textContent).not.toContain('$333')
  expect(document.body.textContent).not.toContain('chat_name')

  const buyers = screen.getByTestId('top-buyers')
  expect(buyers.textContent).toContain('by spend · last 30 days')
  expect(buyers.textContent).toContain('$42.00')
  expect(buyers.textContent).toContain('2 orders · 1 show · new customer')
  expect(buyers.textContent).toContain('1 order · 1 show · returning')
  expect(buyers.textContent).not.toContain('failed')
  expect(within(buyers).getByRole('link', { name: 'mika_r' })).toHaveAttribute('href', '/app/buyers/mika_r')
  expect(await within(buyers).findByTestId('buyer-flag')).toHaveTextContent('VIP')

  const table = screen.getByTestId('shows-table')
  expect(table.textContent).toContain('Sales on Whatnot')
  expect(table.textContent).toContain('Net payout')
  const [, second, matched, unmatched] = within(table).getAllByRole('row')
  expect(second!.textContent).toContain('Counted under the Thu, Nov 12 recording of this show')
  expect(second!.textContent).not.toContain('$42')
  expect(matched!.textContent).toContain('$42 + $10')
  expect(matched!.textContent).toContain('-$5.25')
  expect(unmatched!.textContent).toContain('Not matched to a show on Whatnot')

  // While the 7 days load, the 30 days' numbers stay labelled as 30 days.
  let release: () => void = () => {}
  const held = new Promise<void>((r) => (release = r))
  fetch.mockImplementationOnce(async (url: string) => {
    await held
    return json(whatnot(Number(new URL(url, 'http://x').searchParams.get('days'))))
  })
  fireEvent.click(screen.getByRole('button', { name: '7 days' }))
  await waitFor(() => expect(card).toHaveAttribute('aria-busy', 'true'))
  expect(card.textContent).toContain('Sales on Whatnot · last 30 days')
  expect(card.textContent).toContain('$50.45')
  release()
  await waitFor(() => expect(card.textContent).toContain('$17.00'))
  expect(card.textContent).toContain('Sales on Whatnot · last 7 days')
  expect(card).toHaveAttribute('aria-busy', 'false')
  expect(fetch).toHaveBeenCalledWith('/api/dashboard/whatnot?days=7', expect.anything())
  expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')
})

test('a shop not connected to Whatnot keeps the app’s own numbers', async () => {
  page(['shows.view', 'reports.view'], () => ({ connected: false }))
  expect((await screen.findByTestId('tile-paid')).textContent).toContain('Paid revenue · all shows')
  expect(screen.getByTestId('tile-paid').textContent).toContain('$333.00')
  expect(screen.getByTestId('top-buyers').textContent).toContain('chat_name')
  expect(screen.queryByTestId('whatnot-money')).toBeNull()
})

test('the packing role never asks for Whatnot’s money', async () => {
  const fetch = page(['shows.view'], whatnot)
  await screen.findByTestId('tile-paid')
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith('/api/dashboard/whatnot'))).toBe(false)
  expect(screen.queryByTestId('whatnot-money')).toBeNull()
})

test('when Whatnot’s numbers cannot be read the page says so over the app’s own', async () => {
  page(['shows.view', 'reports.view'], () => ({ error: 'Try again in a minute.' }), 429)
  expect(await screen.findByText(/Could not load Whatnot’s numbers/)).toBeInTheDocument()
  expect(screen.getByTestId('tile-paid').textContent).toContain('Paid revenue · all shows')
})
