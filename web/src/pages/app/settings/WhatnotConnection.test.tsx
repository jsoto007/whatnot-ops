import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import type { WhatnotStatus } from '../../../lib/api'
import { WhatnotBanner, WhatnotConnection } from './WhatnotConnection'

const base: WhatnotStatus = {
  configured: true,
  connected: false,
  username: null,
  connectedAt: null,
  lastCheckedAt: null,
  problem: null,
  ordersMode: 'shadow',
  webhooks: { configured: false, url: null, lastEventAt: null },
  sync: [],
}
const connected: WhatnotStatus = {
  ...base,
  connected: true,
  username: 'think_electronics',
  connectedAt: '2026-11-10T20:00:00Z',
  lastCheckedAt: new Date().toISOString(),
  webhooks: { configured: true, url: 'https://ops.example/api/webhooks/whatnot', lastEventAt: null, waiting: 0 },
  sync: [
    { resource: 'orders', lastOkAt: new Date().toISOString(), failing: false },
    { resource: 'shipments', lastOkAt: null, failing: true },
    { resource: 'some_new_thing', lastOkAt: null, failing: false },
  ],
}

function open(status: WhatnotStatus, manage: boolean, after?: WhatnotStatus) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }))
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      const body = init?.method && init.method !== 'GET' ? (after ?? status) : status
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <WhatnotConnection manage={manage} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test('a server with no token says who does what, with no setting named', async () => {
  open({ ...base, configured: false }, true)
  const state = await screen.findByTestId('whatnot-state')
  expect(state.textContent).toContain('Your developer adds')
  expect(state.textContent).not.toMatch(/WHATNOT_|API_TOKEN|\benv\b/)
  expect(screen.queryByTestId('whatnot-connect')).toBeNull()
})

test('the owner connects; a manager is told the owner can', async () => {
  const calls = open(base, true, connected)
  fireEvent.click(await screen.findByTestId('whatnot-connect'))
  await waitFor(() => expect(screen.getByTestId('whatnot-state').textContent).toContain('Connected as @think_electronics'))
  expect(calls).toContain('POST /api/whatnot/connect')
})

test('a manager sees the state but no connect button', async () => {
  open(base, false)
  await screen.findByTestId('whatnot-state')
  expect(screen.queryByTestId('whatnot-connect')).toBeNull()
  expect(screen.getByText("The shop's owner can connect it.")).toBeTruthy()
})

test('connected: who, the address to give Whatnot, and what was read, all in words', async () => {
  open(connected, true)
  expect((await screen.findByTestId('whatnot-state')).textContent).toContain('Connected as @think_electronics')
  expect(screen.getByTestId('whatnot-webhook-url').textContent).toBe('https://ops.example/api/webhooks/whatnot')
  expect(screen.getByTestId('whatnot-webhooks').textContent).toContain('Nothing received from Whatnot yet.')
  const rows = screen.getByTestId('whatnot-sync').textContent ?? ''
  expect(rows).toContain('Orders and payments')
  expect(rows).toContain('Shipments and trackingnot yet · the last try did not work')
  expect(rows).toContain('Other') // an unknown code never shows as itself
  expect(rows).not.toContain('some_new_thing')
})

test('a rejected token reads as what to do, never as a code', async () => {
  open({ ...connected, problem: 'token_rejected' }, false)
  const state = await screen.findByTestId('whatnot-state')
  expect(state.textContent).toContain('no longer accepts the access token')
  expect(state.textContent).not.toContain('token_rejected')
  expect(screen.queryByTestId('whatnot-disconnect')).toBeNull() // owner only
  expect(screen.queryByTestId('whatnot-webhook-url')).toBeNull()
})

test('the banner shows only when the connection needs a person', () => {
  const { rerender } = render(<WhatnotBanner show={false} />)
  expect(screen.queryByTestId('whatnot-banner')).toBeNull()
  rerender(<WhatnotBanner show />)
  expect(screen.getByTestId('whatnot-banner').textContent).toContain('stopped working')
})

test('the owner chooses compare-only or apply-as-they-arrive; a manager only reads it', async () => {
  const calls = open(connected, true, { ...connected, ordersMode: 'auto' })
  const group = await screen.findByTestId('whatnot-mode')
  const [compare, auto] = Array.from(group.querySelectorAll('input')) as HTMLInputElement[]
  expect(compare!.checked).toBe(true)
  fireEvent.click(auto!)
  await waitFor(() => expect((screen.getByTestId('whatnot-mode').querySelectorAll('input')[1] as HTMLInputElement).checked).toBe(true))
  expect(calls).toContain('PATCH /api/whatnot/settings')
})

test('a manager cannot change how orders are used', async () => {
  open(connected, false)
  const group = await screen.findByTestId('whatnot-mode')
  expect(Array.from(group.querySelectorAll('input')).every((i) => (i as HTMLInputElement).disabled)).toBe(true)
})
