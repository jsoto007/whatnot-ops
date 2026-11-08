import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import type { CapturedRow } from '../src/capture/types'
import { validateBatch } from '../src/upload/schema'
import { LIMITS } from '../src/upload/limits'
import { normaliseUrl } from '../src/upload/settings'
import { BATCH_MAX, UploadError, backoffSeconds, pairDevice, ping, postBatch, uploadOnce, type UploadStore } from '../src/upload/uploader'

const FIXTURES = path.resolve(__dirname, '..', '..', 'shared', 'fixtures')
const fixture = (name: string) => JSON.parse(readFileSync(path.join(FIXTURES, name), 'utf8'))

describe('shared contract (same fixtures as the API tests)', () => {
  it('accepts the valid fixture', () => {
    expect(validateBatch(fixture('batch-valid.json'))).toEqual({ ok: true })
  })
  it('rejects every invalid fixture', () => {
    const names = readdirSync(FIXTURES).filter((f) => f.startsWith('batch-invalid'))
    expect(names.length).toBeGreaterThanOrEqual(3)
    for (const n of names) expect(validateBatch(fixture(n)).ok, n).toBe(false)
  })
})

const S = '2026-10-17T20:00:00.000Z'

function row(showId: string, seq: number): CapturedRow {
  return {
    id: (Date.parse(showId) % 4096).toString(16).padStart(4, '0') + seq.toString(16).padStart(12, '0'),
    showId,
    username: 'u',
    body: 'b' + seq,
    rawText: 'u b' + seq,
    capturedAt: new Date(Date.parse(showId) + seq * 1000).toISOString(),
    seq,
  }
}

function memoryStore(rows: CapturedRow[]): UploadStore & { uploaded: Set<string>; parked: Set<string> } {
  const uploaded = new Set<string>()
  const parked = new Set<string>()
  const pending = () => rows.filter((r) => !uploaded.has(r.id) && !parked.has(r.id))
  return {
    uploaded,
    parked,
    pendingRows: async (limit) => pending().slice(0, limit),
    pendingCount: async () => pending().length,
    markUploaded: async (ids) => ids.forEach((id) => uploaded.add(id)),
    markParked: async (ids) => ids.forEach((id) => parked.add(id)),
    show: async (id) => ({ startedAt: id, url: '/live/x' }),
  }
}

const settings = { serverUrl: 'https://ops.test', apiKey: 'wn_key' }
const ok = () => new Response(JSON.stringify({ accepted: 1 }), { status: 200 })

describe('uploadOnce', () => {
  it('does nothing when not configured', async () => {
    const store = memoryStore([row(S, 1)])
    expect(await uploadOnce(store, { serverUrl: '', apiKey: '' }, vi.fn())).toEqual({ status: 'unconfigured' })
  })

  it('sends one valid batch per show with the bearer key and marks rows uploaded', async () => {
    const A = '2026-10-19T20:00:00.000Z'
    const B = '2026-10-18T20:00:00.000Z'
    const store = memoryStore([row(A, 1), row(A, 2), row(B, 1)])
    const fetchFn = vi.fn(async () => ok())
    const out = await uploadOnce(store, settings, fetchFn)
    expect(out).toEqual({ status: 'sent', sent: 3, remaining: 0 })
    expect(fetchFn).toHaveBeenCalledTimes(2)
    const [url, init] = fetchFn.mock.calls[0]! as unknown as [string, RequestInit]
    expect(url).toBe('https://ops.test/api/ingest')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer wn_key')
    const body = JSON.parse(init.body as string)
    expect(validateBatch(body)).toEqual({ ok: true })
    expect(body.show).toEqual({ id: A, startedAt: A, url: '/live/x' })
    expect(body.rows.map((r: CapturedRow) => r.seq)).toEqual([1, 2])
    expect(store.uploaded.size).toBe(3)
  })

  it('caps a batch and reports what is left', async () => {
    const rows = Array.from({ length: BATCH_MAX + 5 }, (_, i) => row(S, i + 1))
    const store = memoryStore(rows)
    const out = await uploadOnce(store, settings, vi.fn(async () => ok()))
    expect(out).toEqual({ status: 'sent', sent: BATCH_MAX, remaining: 5 })
  })

  it('keeps rows pending when the server is unreachable', async () => {
    const store = memoryStore([row(S, 1)])
    const fetchFn = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    await expect(uploadOnce(store, settings, fetchFn)).rejects.toMatchObject({ kind: 'offline' })
    expect(store.uploaded.size).toBe(0)
  })

  it('reports a bad key as auth and a 400 as rejected with the server detail', async () => {
    await expect(
      postBatch(settings, { version: 1, show: { id: S, startedAt: S }, rows: [row(S, 1)] }, vi.fn(async () => new Response('', { status: 401 }))),
    ).rejects.toMatchObject({ kind: 'auth' })
    const bad = vi.fn(async () => new Response(JSON.stringify({ error: 'invalid batch', details: ['rows/0/id: bad'] }), { status: 400 }))
    await expect(postBatch(settings, { version: 1, show: { id: S, startedAt: S }, rows: [row(S, 1)] }, bad)).rejects.toMatchObject({
      kind: 'rejected',
      message: 'invalid batch; rows/0/id: bad',
    })
    expect(new UploadError('offline', 'x').kind).toBe('offline')
  })

  it('a 429 is a wait, not a refusal: rows stay queued and the Retry-After is kept', async () => {
    const store = memoryStore([row(S, 1)])
    const busy = async () => new Response(JSON.stringify({ error: 'too many attempts' }), { status: 429, headers: { 'Retry-After': '42' } })
    await expect(uploadOnce(store, settings, busy)).rejects.toMatchObject({ kind: 'offline', retryAfterMs: 42_000 })
    expect(store.uploaded.size + store.parked.size).toBe(0)
    expect(await store.pendingCount()).toBe(1)
    // No header: a minute. An absurd header: capped at ten minutes.
    const noHeader = async () => new Response('', { status: 429 })
    const batch = { version: 1 as const, show: { id: S, startedAt: S }, rows: [row(S, 1)] }
    await expect(postBatch(settings, batch, noHeader)).rejects.toMatchObject({ kind: 'offline', retryAfterMs: 60_000 })
    const huge = async () => new Response('', { status: 429, headers: { 'Retry-After': '99999' } })
    await expect(postBatch(settings, batch, huge)).rejects.toMatchObject({ retryAfterMs: 600_000 })
    await expect(ping(settings, busy)).rejects.toMatchObject({ kind: 'offline', retryAfterMs: 42_000 })
  })

  it('sets a refused batch aside and keeps sending the other shows', async () => {
    const A = '2026-10-18T20:00:00.000Z'
    const B = '2026-10-19T20:00:00.000Z'
    const store = memoryStore([row(A, 1), row(A, 2), row(B, 1)])
    const fetchFn = vi.fn(async (_u: string, init: RequestInit) =>
      JSON.parse(String(init.body)).show.id === A ? new Response(JSON.stringify({ error: 'invalid batch' }), { status: 400 }) : ok(),
    )
    const out = await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)
    expect(out).toEqual({ status: 'sent', sent: 1, remaining: 0, parked: 2, detail: 'invalid batch' })
    expect([...store.parked].sort()).toEqual([row(A, 1).id, row(A, 2).id].sort())
    // Nothing is pending any more, so the next flush does not re-send the refused rows.
    expect(await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)).toEqual({ status: 'idle' })
  })

  it('cuts oversized rows to the server limits and refuses redirects', async () => {
    const big = { ...row(S, 1), username: 'u'.repeat(200), body: 'b'.repeat(9000), rawText: 'r'.repeat(9000) }
    const store = memoryStore([big])
    store.show = async () => ({ startedAt: S, url: 'https://whatnot.com/' + 'x'.repeat(600) })
    const fetchFn = vi.fn(async () => ok())
    await uploadOnce(store, settings, fetchFn)
    const init = (fetchFn.mock.calls[0]! as unknown as [string, RequestInit])[1]
    expect(init.redirect).toBe('error')
    const body = JSON.parse(init.body as string)
    expect(validateBatch(body)).toEqual({ ok: true })
    expect(body.rows[0].username).toHaveLength(LIMITS.username)
    expect(body.rows[0].rawText).toHaveLength(LIMITS.rawText)
    expect(body.show.url).toBeUndefined()
  })
})

describe('pairDevice', () => {
  it('posts the code and returns the key the server hands back', async () => {
    const calls: [string, RequestInit][] = []
    const fetchFn = (async (u: string, init: RequestInit) => {
      calls.push([u, init])
      return new Response(JSON.stringify({ key: 'wn_k', device: 'Front laptop', organization: 'Shop' }), { status: 201 })
    }) as unknown as typeof fetch
    const r = await pairDevice('https://ops.test', ' abcd-efgh ', fetchFn)
    expect(r).toEqual({ key: 'wn_k', device: 'Front laptop', organization: 'Shop' })
    expect(calls[0]![0]).toBe('https://ops.test/api/pair')
    expect(JSON.parse(String(calls[0]![1].body))).toEqual({ code: 'abcd-efgh' })
  })
  it("says why a code was refused, and treats a dead server as offline", async () => {
    const refused = (async () => new Response(JSON.stringify({ error: 'that code is wrong, used or expired' }), { status: 404 })) as unknown as typeof fetch
    await expect(pairDevice('https://ops.test', 'ABCDEFGH', refused)).rejects.toMatchObject({ kind: 'rejected', message: 'that code is wrong, used or expired' })
    const down = (async () => {
      throw new TypeError('Failed to fetch')
    }) as unknown as typeof fetch
    await expect(pairDevice('https://ops.test', 'ABCDEFGH', down)).rejects.toBeInstanceOf(UploadError)
    await expect(pairDevice('https://ops.test', 'ABCDEFGH', down)).rejects.toMatchObject({ kind: 'offline' })
  })
})

describe('helpers', () => {
  it('backs off 5s, 10s, 20s … capped at 5 minutes', () => {
    expect([1, 2, 3, 4, 10].map(backoffSeconds)).toEqual([5, 10, 20, 40, 300])
  })
  it('normalises the server url', () => {
    expect(normaliseUrl(' https://ops.test/// ')).toBe('https://ops.test')
  })
})

function memoryKv() {
  const m = new Map<string, unknown>()
  return { m, get: async <T,>(k: string) => m.get(k) as T | undefined, set: async (k: string, v: unknown) => void m.set(k, v), del: async (k: string) => void m.delete(k) }
}

describe('serverOrigin', () => {
  it('accepts https and localhost http, keeps only the origin', async () => {
    const { serverOrigin } = await import('../src/upload/settings')
    expect(serverOrigin(' https://ops.example.com/app/ ')).toBe('https://ops.example.com')
    expect(serverOrigin('http://127.0.0.1:5056/')).toBe('http://127.0.0.1:5056')
    expect(serverOrigin('http://localhost:5050')).toBe('http://localhost:5050')
    expect(serverOrigin('')).toBe('')
  })
  it('refuses plain http, other schemes, credentials and junk', async () => {
    const { serverOrigin } = await import('../src/upload/settings')
    expect(() => serverOrigin('http://ops.example.com')).toThrow(/https/)
    expect(() => serverOrigin('ftp://ops.example.com')).toThrow(/https/)
    expect(() => serverOrigin('javascript:alert(1)')).toThrow()
    expect(() => serverOrigin('https://user:pw@ops.example.com')).toThrow(/password/)
    expect(() => serverOrigin('not a url')).toThrow(/web address/)
  })
})

describe('loadSettings', () => {
  const storage = (vals: Record<string, string>) => {
    const removed: string[] = []
    return Object.assign({ get: async () => vals, remove: async (k: string) => void removed.push(k) }, { removed }) as unknown as typeof chrome.storage.local & { removed: string[] }
  }
  beforeEach(() => {
    vi.stubGlobal('chrome', { runtime: { getURL: (p: string) => 'chrome-extension://x/' + p } })
  })
  const noDefaults = vi.fn(async () => {
    throw new Error('404')
  })
  it('uses the baked-in default when nothing is saved, but never an http one', async () => {
    const { loadSettings } = await import('../src/upload/settings')
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ serverUrl: 'https://ops.test/' }), { status: 200 }))
    expect(await loadSettings(storage({}), fetchFn, memoryKv())).toEqual({ serverUrl: 'https://ops.test', apiKey: '' })
    const plain = vi.fn(async () => new Response(JSON.stringify({ serverUrl: 'http://ops.test' }), { status: 200 }))
    expect(await loadSettings(storage({}), plain, memoryKv())).toEqual({ serverUrl: '', apiKey: '' })
  })
  it('moves a key from chrome.storage.local into IndexedDB, bound to its server', async () => {
    const { loadSettings, KV_CONNECTION } = await import('../src/upload/settings')
    const kv = memoryKv()
    const st = storage({ cs_server_url: 'https://mine.test', cs_api_key: 'k' })
    expect(await loadSettings(st, noDefaults, kv)).toEqual({ serverUrl: 'https://mine.test', apiKey: 'k' })
    expect(st.removed).toEqual(['cs_api_key'])
    expect(kv.m.get(KV_CONNECTION)).toEqual({ apiKey: 'k', origin: 'https://mine.test' })
  })
  it('never hands the key to a different server than the one it was issued for', async () => {
    const { loadSettings, saveConnection } = await import('../src/upload/settings')
    const kv = memoryKv()
    await saveConnection({ apiKey: 'k', origin: 'https://mine.test', organization: 'Shop' }, kv)
    expect((await loadSettings(storage({ cs_server_url: 'https://mine.test' }), noDefaults, kv)).apiKey).toBe('k')
    expect(await loadSettings(storage({ cs_server_url: 'https://evil.test' }), noDefaults, kv)).toEqual({ serverUrl: 'https://evil.test', apiKey: '' })
  })
  it('says when the laptop is connected to a different shop than before', async () => {
    const { saveConnection } = await import('../src/upload/settings')
    const kv = memoryKv()
    expect(await saveConnection({ apiKey: 'a', origin: 'https://x.test', organization: 'Shop A' }, kv)).toBe(false)
    expect(await saveConnection({ apiKey: 'b', origin: 'https://x.test', organization: 'Shop A' }, kv)).toBe(false)
    expect(await saveConnection({ apiKey: 'c', origin: 'https://x.test', organization: 'Shop B' }, kv)).toBe(true)
  })
})
