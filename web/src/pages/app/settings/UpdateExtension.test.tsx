import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { Device } from '../../../lib/api'
import { isNewer, resetThisLaptop } from '../../../lib/extension'
import { Laptops } from './Laptops'
import { UpdateBanner, UpdateExtension } from './UpdateExtension'

const ID = 'abcdefghijklmnopabcdefghijklmnop'

/** What portal.js posts when Chat Scraper 1.5.0+ runs on this laptop. */
function announce(version: string) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { source: 'chat-scraper', type: 'here', id: ID, version }, origin: location.origin, source: window }))
  })
}

/** A stand-in for the extension's background: answers hello, and reload swaps in the files on "disk". */
function fakeExtension(state: { version: string; filesVersion: string; recording?: boolean }) {
  const calls: string[] = []
  const sendMessage = vi.fn((id: string, msg: { cmd: string }, cb: (r: unknown) => void) => {
    calls.push(msg.cmd)
    if (id !== ID) return cb(undefined)
    if (msg.cmd === 'reload') {
      state.version = state.filesVersion
      return cb({ ok: true })
    }
    cb({ version: state.version, filesVersion: state.filesVersion, recording: !!state.recording })
  })
  vi.stubGlobal('chrome', { runtime: { sendMessage } })
  return { calls, state }
}

function wrap(ui: ReactNode, api: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = new URL(url, 'http://x').pathname
      return new Response(JSON.stringify(api[path] ?? {}), { status: 200 })
    }),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const EXT = { '/api/extension': { available: true, version: '1.5.0', serverUrl: 'http://localhost' } }

beforeEach(() => {
  resetThisLaptop()
  localStorage.clear()
})
afterEach(() => vi.unstubAllGlobals())

test('versions compare as numbers', () => {
  expect(isNewer('1.10.0', '1.9.9')).toBe(true)
  expect(isNewer('1.5.0', '1.5')).toBe(false)
  expect(isNewer('1.5.0', null)).toBe(false)
})

test('waits for the new files, reloads, and confirms the new version is running', async () => {
  const ext = fakeExtension({ version: '1.5.0', filesVersion: '1.5.0', recording: true })
  wrap(<UpdateExtension latest="1.6.0" onClose={() => {}} />)
  announce('1.5.0')
  await waitFor(() => expect(screen.getByTestId('update-files')).toHaveTextContent('Waiting for the new files. The folder still has 1.5.0.'))
  expect(screen.getByTestId('update-reload')).toBeDisabled()
  expect(screen.getByText(/A show is recording on this laptop/)).toBeInTheDocument()

  // The new download lands in the folder: the extension reports it before the reload.
  ext.state.filesVersion = '1.6.0'
  await waitFor(() => expect(screen.getByTestId('update-files')).toHaveTextContent('New files found (1.6.0)'), { timeout: 4000 })
  fireEvent.click(screen.getByTestId('update-reload'))
  await waitFor(() => expect(screen.getByTestId('update-done')).toHaveTextContent('Chat Scraper 1.6.0 is running'), { timeout: 4000 })
  expect(ext.calls).toContain('reload')
})

test('a laptop on an older build gets the manual steps, no reload button', async () => {
  wrap(<UpdateExtension latest="1.6.0" forName="Front laptop" onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'Update Chat Scraper' })
  expect(dialog).toHaveTextContent('Do this on Front laptop')
  expect(dialog).toHaveTextContent("Don't remove the extension to update it.")
  expect(dialog).toHaveTextContent('click ↻ on Chat Scraper')
  expect(screen.queryByTestId('update-reload')).not.toBeInTheDocument()
  expect(within(dialog).getByTestId('update-download')).toHaveAttribute('href', '/api/extension/download')
})

test('the banner shows only when this laptop is behind, and "Not now" holds until the next version', async () => {
  fakeExtension({ version: '1.4.9', filesVersion: '1.4.9' })
  const { unmount } = wrap(<UpdateBanner enabled />, EXT)
  expect(screen.queryByTestId('update-banner')).not.toBeInTheDocument()
  announce('1.4.9')
  const banner = await screen.findByTestId('update-banner')
  expect(banner).toHaveTextContent('Chat Scraper 1.5.0 is ready. This laptop has 1.4.9.')
  fireEvent.click(within(banner).getByRole('button', { name: 'Update' }))
  expect(screen.getByRole('dialog', { name: 'Update Chat Scraper' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  fireEvent.click(within(banner).getByRole('button', { name: 'Not now' }))
  expect(screen.queryByTestId('update-banner')).not.toBeInTheDocument()
  expect(localStorage.getItem('wn.extension-update-dismissed')).toBe('1.5.0')
  unmount()

  resetThisLaptop()
  wrap(<UpdateBanner enabled />, EXT)
  announce('1.5.0') // up to date: nothing to say
  await new Promise((r) => setTimeout(r, 50))
  expect(screen.queryByTestId('update-banner')).not.toBeInTheDocument()
})

test('a page message from anything but Chat Scraper is ignored', async () => {
  wrap(<UpdateBanner enabled />, EXT)
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { source: 'chat-scraper', type: 'here', id: 'not-an-id', version: '1.0.0' }, origin: location.origin, source: window }))
    window.dispatchEvent(new MessageEvent('message', { data: { source: 'chat-scraper', type: 'here', id: ID, version: '1.0.0' }, origin: 'https://evil.example', source: window }))
  })
  await new Promise((r) => setTimeout(r, 50))
  expect(screen.queryByTestId('update-banner')).not.toBeInTheDocument()
})

test('Laptops lists each version and marks the ones behind', async () => {
  const seen = new Date().toISOString()
  const devices: Device[] = [
    { id: 1, name: 'Front', keyPrefix: 'wn_a', createdAt: seen, lastSeenAt: seen, revoked: false, extensionVersion: '1.5.0', createdBy: 'O', makerLostAccess: false },
    { id: 2, name: 'Back', keyPrefix: 'wn_b', createdAt: seen, lastSeenAt: seen, revoked: false, extensionVersion: '1.4.2', createdBy: 'O', makerLostAccess: false },
    { id: 3, name: 'Old', keyPrefix: 'wn_c', createdAt: seen, lastSeenAt: seen, revoked: false, extensionVersion: null, createdBy: 'O', makerLostAccess: false },
    { id: 4, name: 'New', keyPrefix: 'wn_d', createdAt: seen, lastSeenAt: null, revoked: false, extensionVersion: null, createdBy: 'O', makerLostAccess: false },
  ]
  wrap(<Laptops manage />, { ...EXT, '/api/devices': { devices } })
  const rows = await screen.findAllByTestId('device-version')
  const byName = (n: string) => screen.getByText(n).closest('tr')!.querySelector('[data-testid="device-version"]')!
  expect(rows).toHaveLength(4)
  expect(byName('Front')).toHaveTextContent(/^1\.5\.0$/)
  expect(byName('Back')).toHaveTextContent('1.4.2Update')
  expect(byName('Old')).toHaveTextContent('olderUpdate')
  expect(byName('New')).toHaveTextContent('—')
  fireEvent.click(within(byName('Back') as HTMLElement).getByRole('button', { name: 'Update' }))
  expect(screen.getByRole('dialog', { name: 'Update Chat Scraper' })).toHaveTextContent('Do this on Back')

  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  fakeExtension({ version: '1.4.2', filesVersion: '1.4.2' })
  announce('1.4.2')
  expect(screen.getByTestId('this-laptop')).toHaveTextContent('This laptop has Chat Scraper 1.4.2.')
  expect(screen.getByTestId('update-this-laptop')).toHaveTextContent('Update this laptop to v1.5.0')
})
