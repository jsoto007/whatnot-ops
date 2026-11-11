import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Show } from '../../lib/api'
import { Reports } from './Reports'

const auth: AuthState = {
  me: {
    user: { id: 1, email: 'm@x.test', name: 'Mia', role: 'viewer', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo', slug: 'demo', hostUsername: null },
    permissions: ['reports.view'],
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => p === 'reports.view',
  setMe: () => {},
  logout: async () => {},
}

const day = (back: number) => new Date(Date.now() - back * 86_400_000).toLocaleDateString('en-CA', { timeZone: 'America/Chicago' })
const shows: Show[] = Array.from({ length: 14 }, (_, i) => ({
  id: i + 1,
  key: `k${i}`,
  // 3 shows in the last 30 days, 11 older ones
  date: day(i < 3 ? i * 5 : 40 + i),
  startedAt: '2026-11-08T02:00:00Z',
  updatedAt: '2026-11-08T02:00:00Z',
  sourceUrl: null,
  messageCount: 1,
}))

function open() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ shows }), { status: 200, headers: { 'Content-Type': 'application/json' } })),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter>
          <Reports />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

test('the date-range report starts at the last 30 days', async () => {
  open()
  expect((await screen.findByTestId('overview-range')).textContent).toContain('shows in these dates')
  await screen.findByText(/^3 shows in these dates/)
  const link = screen.getByTestId('download-overview')
  expect(link.getAttribute('href')).toBe(`/api/reports/overview.xlsx?from=${day(30)}&to=`)
})

test('a range with too many shows cannot be downloaded, and says why', async () => {
  open()
  await screen.findByText(/^3 shows in these dates/)
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '' } })
  const link = screen.getByTestId('download-overview')
  expect(link.getAttribute('href')).toBeNull()
  expect(link.getAttribute('aria-disabled')).toBe('true')
  expect(screen.getByTestId('overview-range').textContent).toBe('These dates cover 14 shows. One report holds up to 12: pick a shorter range.')
})
