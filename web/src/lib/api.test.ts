import { ApiError, api } from './api'

afterEach(() => {
  document.cookie = 'csrf=; Max-Age=0'
})

test('writes fetch the CSRF cookie first and echo it in the header', async () => {
  const fetchFn = vi.fn(async (url: string) => {
    if (url === '/api/auth/csrf') {
      document.cookie = 'csrf=' + 'a'.repeat(43)
      return new Response('{"token":"x"}', { status: 200 })
    }
    return new Response('{"ok":true}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchFn)
  await api('POST', '/api/team/invites', { email: 'x' })
  const [, init] = fetchFn.mock.calls[1]! as unknown as [string, RequestInit]
  expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('a'.repeat(43))
  expect(init.credentials).toBe('same-origin')
})

test('API errors carry the server message and status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error":"wrong email or password"}', { status: 401 })))
  await expect(api('GET', '/api/auth/me')).rejects.toMatchObject({ status: 401, message: 'wrong email or password' })
  expect(new ApiError(500, 'x').status).toBe(500)
})
