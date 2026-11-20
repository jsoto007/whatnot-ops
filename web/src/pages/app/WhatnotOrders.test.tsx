import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import type { WhatnotCompare } from '../../lib/api'
import { WhatnotOrders } from './WhatnotOrders'

const SHOW = { id: 4, title: 'Friday night', status: 'ended', startsAt: '2026-11-08T02:00:00Z', endedAt: '2026-11-08T05:00:00Z', url: null, ordersReadAt: new Date().toISOString() }
const row = (buyer: string, status: string, action: string, note: string | null = null) => ({ buyer, item: 'CHOOSE ANY PRODUCT YOU WANT #73', quantity: 1, priceCents: 1700, status, externalId: '1462657228', action, saleId: null, note })
const compared: WhatnotCompare = {
  connected: true,
  mode: 'shadow',
  link: 'auto',
  show: SHOW,
  choices: [SHOW, { ...SHOW, id: 5, title: 'Saturday' }],
  complete: true,
  info: { orders: 3, giveaways: 1, noBuyer: 0 },
  summary: { unchanged: 1, decline: 1, record: 1 },
  rows: [row('lani808', 'paid', 'unchanged'), row('kat.c', 'failed', 'decline'), row('newbie', 'paid', 'record')] as WhatnotCompare['rows'],
  uncovered: [{ saleId: 9, buyer: 'ghost', item: 'Red bundle', quantity: 1, priceCents: 2500, status: 'paid' }],
}

function open(first: WhatnotCompare, canApply: boolean, after?: WhatnotCompare) {
  const calls: { method: string; url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }))
      const method = init?.method ?? 'GET'
      calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined })
      return new Response(JSON.stringify(method === 'GET' ? first : (after ?? first)), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <WhatnotOrders showId="7" canApply={canApply} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test('a shop not connected to Whatnot sees nothing here', async () => {
  const calls = open({ connected: false }, true)
  await waitFor(() => expect(calls.length).toBe(1))
  expect(screen.queryByTestId('whatnot-orders')).toBeNull()
})

test("Whatnot's orders next to the sales, in words, and what Apply will do", async () => {
  open(compared, true)
  const state = await screen.findByTestId('whatnot-orders-state')
  expect(state.textContent).toContain('Friday night · Ended · orders read just now · 3 orders, 1 giveaway')
  expect(screen.getByTestId('whatnot-summary').textContent).toBe('1 nothing to change · 1 mark the payment declined · 1 record the sale')
  const table = screen.getByTestId('whatnot-rows').textContent ?? ''
  expect(table).toContain('kat.c')
  expect(table).toContain('payment failed')
  expect(table).toContain('record the sale')
  // No code reaches the page: not the status, not the action.
  for (const code of ['pending_fulfillment', 'mark_paid', 'decline ', 'void']) expect(table).not.toContain(code)
  expect(screen.getByTestId('whatnot-uncovered').textContent).toContain('ghost')
  // Two rows change something, and the show is over: the sale with no order counts too.
  expect(screen.getByTestId('whatnot-apply').textContent).toBe('Apply 3 changes')
})

test('Apply posts once and says what was done', async () => {
  const calls = open(compared, true, { ...compared, summary: { unchanged: 3 }, rows: [], uncovered: [], applied: { unchanged: 1, decline: 1, recorded: 1 } })
  fireEvent.click(await screen.findByTestId('whatnot-apply'))
  await waitFor(() => expect(screen.getByTestId('whatnot-applied').textContent).toBe('1 payment declined · 1 newly recorded'))
  expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/api/shows/7/whatnot/apply'])
  expect(screen.getByTestId('whatnot-apply').textContent).toBe('Nothing to apply')
})

test('a declined win with no product that was paid after all goes back to waiting, in words', async () => {
  const waiting = { ...compared, summary: { reopen: 1, skip: 1 }, rows: [row('made_up_ann', 'failed', 'skip'), row('made_up_ann', 'paid', 'reopen')] as WhatnotCompare['rows'], uncovered: [] }
  open(waiting, true, { ...waiting, summary: { unchanged: 1, skip: 1 }, rows: [], applied: { reopen: 1, skip: 1, gave_way: 1 } })
  expect((await screen.findByTestId('whatnot-summary')).textContent).toBe('1 wait for its product again · 1 left alone')
  expect(screen.getByTestId('whatnot-rows').textContent).not.toContain('reopen')
  expect(screen.getByTestId('whatnot-apply').textContent).toBe('Apply 1 change')
  fireEvent.click(screen.getByTestId('whatnot-apply'))
  // ...and a sale that gave way to the auction's winner is said in words too
  await waitFor(() => expect(screen.getByTestId('whatnot-applied').textContent).toBe('1 waiting for a product again · 1 cancelled, the auction went to another win'))
})

test('someone who may not apply sees the comparison and no buttons', async () => {
  open(compared, false)
  await screen.findByTestId('whatnot-rows')
  for (const id of ['whatnot-apply', 'whatnot-refresh', 'whatnot-link']) expect(screen.queryByTestId(id)).toBeNull()
})

test('a show still live does not call out sales with no order, and they do not count as changes', async () => {
  open({ ...compared, complete: false, show: { ...SHOW, status: 'live', endedAt: null } }, true)
  await screen.findByTestId('whatnot-rows')
  expect(screen.getByTestId('whatnot-apply').textContent).toBe('Apply 2 changes')
  expect(screen.getByText('Their orders may simply not have arrived yet.')).toBeTruthy()
})

test('a show Whatnot left live is said to be treated as over, in words', async () => {
  open({ ...compared, show: { ...SHOW, status: 'live', endedAt: null, assumedEnd: { by: 'time', at: '2026-11-08T14:00:00Z' } } }, true)
  const line = (await screen.findByTestId('whatnot-orders-state')).textContent ?? ''
  expect(line).toContain('Live (treated as over: 12 hours have passed since it began')
  expect(line).not.toContain('(time)')
})

test('with hardly a buyer named the page says so and Apply is off', async () => {
  open({ ...compared, info: { orders: 3, giveaways: 0, noBuyer: 3, blind: true } }, true)
  const note = (await screen.findByTestId('whatnot-no-buyers')).textContent ?? ''
  expect(note).toContain('none of this show')
  expect(note).not.toContain('permission')
  expect((screen.getByTestId('whatnot-apply') as HTMLButtonElement).disabled).toBe(true)
})

test('one order with no buyer is left out and the rest can still be applied', async () => {
  open({ ...compared, info: { orders: 40, giveaways: 0, noBuyer: 1, blind: false, unreadable: 2 } }, true)
  const note = (await screen.findByTestId('whatnot-no-buyers')).textContent ?? ''
  expect(note).toContain('left out')
  expect(note).not.toContain('permission')
  expect(screen.getByTestId('whatnot-unreadable').textContent).toContain('2 items named no listing')
  expect((screen.getByTestId('whatnot-apply') as HTMLButtonElement).disabled).toBe(false)
})

test('orders Whatnot listed no items for are said and named, not dropped', async () => {
  const named = ['#1000000101 from kat.c', '#1000000102 from sam', '#1000000103 from lani808']
  open({ ...compared, info: { orders: 40, giveaways: 0, noBuyer: 0, blind: false, unreadable: 1, noItems: 3, leftOut: 4, leftOutOrders: named } }, true)
  expect((await screen.findByTestId('whatnot-no-items')).textContent).toContain('Whatnot listed no items for 3 orders')
  const which = screen.getByTestId('whatnot-left-out-orders').textContent ?? ''
  expect(which).toContain(`${named.join(', ')} and 1 more`)
  expect(which).not.toContain('leftOut')
})

test('an unmatched show can be matched by hand', async () => {
  const calls = open({ connected: true, mode: 'shadow', link: null, show: null, choices: [SHOW], rows: [], uncovered: [], summary: {} }, true, compared)
  expect((await screen.findByTestId('whatnot-orders-state')).textContent).toContain('not matched to a show on Whatnot yet')
  fireEvent.change(screen.getByLabelText('Show on Whatnot'), { target: { value: '4' } })
  fireEvent.click(screen.getByTestId('whatnot-link'))
  await screen.findByTestId('whatnot-rows')
  expect(calls.find((c) => c.method === 'POST')).toEqual({ method: 'POST', url: '/api/shows/7/whatnot/link', body: { whatnotShowId: 4 } })
})

test('an order that disagrees with a closed sale counts as a change: Apply sends it to Review', async () => {
  const conflict = { ...row('kat.c', 'paid', 'unmatched', 'cancelled here, but paid on Whatnot'), saleId: 12 }
  open({ ...compared, summary: { unmatched: 1 }, rows: [conflict] as WhatnotCompare['rows'], uncovered: [] }, true)
  await screen.findByTestId('whatnot-rows')
  expect(screen.getByTestId('whatnot-apply').textContent).toBe('Apply 1 change')
  expect(screen.getByTestId('whatnot-rows').textContent).toContain('send to Review (cancelled here, but paid on Whatnot)')
})

test('applying with nothing changed says so', async () => {
  open(compared, true, { ...compared, applied: { unchanged: 3, skip: 1 } })
  fireEvent.click(await screen.findByTestId('whatnot-apply'))
  await waitFor(() => expect(screen.getByTestId('whatnot-applied').textContent).toBe('Nothing needed changing.'))
})
