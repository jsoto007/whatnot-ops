import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { Device } from '../../../lib/api'
import { resetThisLaptop } from '../../../lib/extension'
import { Laptops } from './Laptops'

/** Settings → Laptops says who made each key (it stops working when they lose access). */

const at = '2026-11-07T15:00:00Z'
const device = (id: number, name: string, createdBy: string | null, revoked = false, makerLostAccess = false): Device => ({
  id,
  name,
  keyPrefix: `wn_${id}`,
  createdAt: at,
  lastSeenAt: null,
  revoked,
  extensionVersion: null,
  createdBy,
  makerLostAccess,
})

function show(devices: Device[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = new URL(url, 'http://x').pathname
      return new Response(JSON.stringify(path === '/api/devices' ? { devices } : {}), { status: 200 })
    }),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Laptops manage />
    </QueryClientProvider>,
  )
}

beforeEach(() => resetThisLaptop())
afterEach(() => vi.unstubAllGlobals())

test('each laptop shows who made its key; old ones say unknown and suggest re-pairing', async () => {
  show([device(1, 'Front laptop', 'Mia'), device(2, 'Back laptop', null)])
  const table = await screen.findByTestId('devices')
  const row = (name: string) => within(table).getByText(name).closest('tr') as HTMLElement
  expect(within(row('Front laptop')).getByTestId('device-maker')).toHaveTextContent(/by Mia$/)
  expect(within(row('Back laptop')).getByTestId('device-maker')).toHaveTextContent(/by unknown$/)
  expect(screen.getByTestId('unknown-maker-note')).toHaveTextContent(/disconnect it and connect it again with a code/)
})

test('no re-pair note when every working laptop has a known maker', async () => {
  show([device(1, 'Front laptop', 'Mia'), device(2, 'Gone laptop', null, true)])
  await screen.findByTestId('devices')
  expect(screen.queryByTestId('unknown-maker-note')).toBeNull()
})

test('a key whose maker already lost access is flagged, not shown as covered', async () => {
  show([device(1, 'Front laptop', 'Mia', false, true), device(2, 'Gone laptop', 'Mia', true, true)])
  const table = await screen.findByTestId('devices')
  const row = (name: string) => within(table).getByText(name).closest('tr') as HTMLElement
  expect(within(row('Front laptop')).getByTestId('device-maker')).toHaveTextContent(/by Mia · who no longer has access$/)
  expect(within(row('Gone laptop')).getByTestId('device-maker')).toHaveTextContent(/by Mia$/)
  expect(screen.getByTestId('unknown-maker-note')).toBeInTheDocument()
})
