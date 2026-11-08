import { ApiError, api } from './api'

test('writes fetch the CSRF token once, echo it in the header, and refetch it after a CSRF 403', async () => {
  let issued = 0
  let rejectNext = false
  const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: `t${++issued}`.padEnd(43, 'a') }), { status: 200 })
    if (rejectNext) {
      rejectNext = false
      return new Response('{"error":"missing or invalid CSRF token"}', { status: 403 })
    }
    return new Response(JSON.stringify({ ok: true, sent: (init?.headers as Record<string, string>)['X-CSRF-Token'] }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchFn)
  await api('POST', '/api/team/invites', { email: 'x' })
  await api('PATCH', '/api/team/organization', { hostUsername: 'y' })
  const [, init] = fetchFn.mock.calls[1]! as unknown as [string, RequestInit]
  expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('t1'.padEnd(43, 'a'))
  expect(init.credentials).toBe('same-origin')
  expect(fetchFn.mock.calls.filter(([u]) => u === '/api/auth/csrf')).toHaveLength(1)
  expect(document.cookie).not.toContain('csrf')

  rejectNext = true // e.g. the session cookie expired: a new token, then the write once more
  const r = await api<{ sent: string }>('POST', '/api/team/invites', { email: 'z' })
  expect(r.sent).toBe('t2'.padEnd(43, 'a'))
  expect(fetchFn.mock.calls.filter(([u]) => u === '/api/auth/csrf')).toHaveLength(2)
})

test('API errors carry the server message and status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error":"wrong email or password"}', { status: 401 })))
  await expect(api('GET', '/api/auth/me')).rejects.toMatchObject({ status: 401, message: 'wrong email or password' })
  expect(new ApiError(500, 'x').status).toBe(500)
})
