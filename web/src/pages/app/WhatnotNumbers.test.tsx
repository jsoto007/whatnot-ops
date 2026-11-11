import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { WhatnotFigures, WhatnotLedger as Ledger, WhatnotRecap as Recap } from '../../lib/api'
import { WhatnotLedger, WhatnotRecap } from './WhatnotNumbers'

const figures: WhatnotFigures = {
  netCents: 1260,
  kinds: [
    { kind: 'ORDER_EARNINGS', lines: 2, cents: 3682 },
    { kind: 'TIP', lines: 1, cents: 300 },
    { kind: 'SHIPPING_CHARGE', lines: 1, cents: -525 },
    { kind: 'ORDER_REFUND', lines: 1, cents: -2197 },
    { kind: 'SOMETHING_NEW', lines: 1, cents: 0 },
  ],
  fees: { commission: 336, commission_tax: 0, processing_fee: 182, processing_fee_tax: 0, seller_shipping: 0, coupon: 0 },
  itemPricesCents: 4200,
  costOfGoodsCents: null,
  through: '2026-11-09T10:00:00+00:00',
}
const recap: Recap = {
  title: 'Friday night',
  resultsStatus: 'GENERATED',
  resultsAt: new Date().toISOString(),
  results: { salesCents: 123456, estimatedEarningsCents: 101010, averageOrderValueCents: 546, ordersCount: 226, buyersCount: 73, firstTimeBuyersCount: 19, returningBuyersCount: 54, distinctViewersCount: 3877, maxConcurrentViewers: 412, streamMinutes: 184, showImpressionCount: 48211, showClickthroughRate: 7.13, totalViews: 9120, buyerSharesCount: 31, giveawayOrdersCount: 21, giveawaySpendCents: -11025, averageRating: 4.94 },
  ledger: figures,
}

const page = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )

afterEach(() => vi.unstubAllGlobals())

test("a show's results and ledger, as Whatnot states them, in words", () => {
  page(<WhatnotRecap w={recap} />)
  expect(screen.getByTestId('wn-sales').textContent).toBe('Sales$1,234.56226 orders · $5.46 each on average')
  expect(screen.getByTestId('wn-earnings').textContent).toContain('$1,010.10')
  expect(screen.getByTestId('wn-buyers').textContent).toBe('Buyers7319 first time · 54 returning')
  expect(screen.getByTestId('wn-viewers').textContent).toBe('Viewers3,877412 at the peak · 184 min live')
  expect(screen.getByTestId('wn-more').textContent).toContain('7.13% tapped in')
  expect(screen.getByTestId('wn-more').textContent).toContain('21 giveaways ($110.25 in shipping)')
  const ledger = screen.getByTestId('wn-ledger').textContent ?? ''
  expect(ledger).toContain('Order earnings · 2 lines$36.82')
  expect(ledger).toContain('Refunds · 1 line−$21.97')
  expect(ledger).toContain('Shipping charged to the shop · 1 line−$5.25')
  expect(ledger).toContain('Other') // a kind the app does not know never shows as its code
  for (const code of ['ORDER_EARNINGS', 'SOMETHING_NEW', 'commission_tax', 'GENERATED']) expect(ledger).not.toContain(code)
  expect(screen.getByTestId('wn-ledger-net').textContent).toBe('$12.60')
  // Only the fees that are not zero, and never added to or taken from the net by the app.
  expect(screen.getByTestId('wn-ledger-fees').textContent).toBe('Fees Whatnot lists on these orders: Commission $3.36 · Payment processing $1.82.')
})

test('results that are not there yet say why, and a show with no ledger lines says where to read it', () => {
  page(<WhatnotRecap w={{ ...recap, resultsStatus: 'PENDING', results: null, ledger: null }} />)
  expect(screen.getByTestId('wn-results-status').textContent).toContain('still working out')
  expect(screen.getByTestId('wn-results-status').textContent).not.toContain('PENDING')
  expect(screen.getByTestId('wn-ledger-empty').textContent).toContain('read once a day')
})

test('a negative net reads as a minus amount', () => {
  page(<WhatnotRecap w={{ ...recap, ledger: { ...figures, netCents: -225 } }} />)
  expect(screen.getByTestId('wn-ledger-net').textContent).toBe('−$2.25')
})

function ledger(first: Ledger, canRead: boolean, after?: Ledger) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }))
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      return new Response(JSON.stringify(init?.method === 'POST' ? (after ?? first) : first), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  page(<WhatnotLedger canRead={canRead} />)
  return calls
}
const held: Ledger = { connected: true, lines: 5, building: false, lastImportedAt: new Date().toISOString(), notAllowed: false, lastFailed: false, unreadable: false, days: 30, recent: figures, shows: [{ showId: 7, title: 'Friday night', startedAt: '2026-11-08T02:00:00Z', netCents: 1260, lines: 5 }, { showId: null, title: '', startedAt: null, netCents: -525, lines: 1 }] }

test('Reports: the last 30 days and the net of each show', async () => {
  ledger(held, true)
  expect((await screen.findByTestId('whatnot-ledger-state')).textContent).toContain('5 ledger lines read')
  expect(screen.getByTestId('ledger-recent-net').textContent).toBe('$12.60')
  const rows = screen.getByTestId('ledger-shows').textContent ?? ''
  expect(rows).toContain('Friday night')
  expect(rows).toContain('Untitled show')
  expect(rows).toContain('−$5.25')
  expect(screen.getByRole('link', { name: 'Friday night' }).getAttribute('href')).toBe('/app/shows/7?tab=analytics')
})

test('a manager reads it now; while Whatnot builds the file the button checks again', async () => {
  const calls = ledger({ ...held, lines: 0, recent: null, shows: [], lastImportedAt: null }, true, { ...held, building: true })
  expect((await screen.findByTestId('whatnot-ledger-state')).textContent).toContain('Nothing read yet')
  fireEvent.click(screen.getByTestId('whatnot-ledger-read'))
  await waitFor(() => expect(screen.getByTestId('whatnot-ledger-state').textContent).toContain('building the report now'))
  expect(calls).toContain('POST /api/whatnot/ledger/read')
  expect(screen.getByTestId('whatnot-ledger-read').textContent).toBe('Check again')
})

test('a viewer reads the numbers but has no button; a missing permission says what to do', async () => {
  ledger({ ...held, notAllowed: true }, false)
  expect((await screen.findByTestId('whatnot-ledger-state')).textContent).toContain('may not read reports yet')
  expect(screen.queryByTestId('whatnot-ledger-read')).toBeNull()
})

test('a shop not connected to Whatnot sees nothing', async () => {
  const calls = ledger({ connected: false }, true)
  await waitFor(() => expect(calls.length).toBe(1))
  expect(screen.queryByTestId('whatnot-ledger')).toBeNull()
})

test('a number Whatnot did not send is left out, not shown as a dash', () => {
  page(<WhatnotRecap w={{ ...recap, resultsAt: null, results: { salesCents: 9300, ordersCount: 4, totalViews: 50 } }} />)
  expect(screen.getByTestId('wn-more').textContent).toBe('50 views')
})

test('lines in another currency are said, not silently added', () => {
  page(<WhatnotRecap w={{ ...recap, ledger: { ...figures, currency: 'USD', otherCurrencyLines: 2 } }} />)
  expect(screen.getByTestId('wn-ledger-other-currency').textContent).toContain('2 lines are in another currency')
})

test('lines the app could not read, or that repeat a transaction, are said on the page', async () => {
  ledger({ ...held, skippedLines: 2, repeatedLines: 1 }, true)
  const note = await screen.findByTestId('whatnot-ledger-short')
  expect(note.textContent).toContain('2 lines could not be read.')
  expect(note.textContent).toContain('1 line repeats a transaction already counted and was not added again.')
})

test('a token that may not read reports says so instead of "not read yet"', () => {
  page(<WhatnotRecap w={{ ...recap, resultsNotAllowed: true, resultsStatus: null, results: null }} />)
  expect(screen.getByTestId('wn-results-status').textContent).toContain('may not read reports yet')
})
