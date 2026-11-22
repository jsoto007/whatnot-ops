import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import type { CapturedRow } from '../src/capture/types'
import { validateBatch } from '../src/upload/schema'
import { LIMITS } from '../src/upload/limits'
import { normaliseUrl } from '../src/upload/settings'
import { BATCH_MAX, UploadError, backoffSeconds, pairDevice, ping, postBatch, refusedUnknownShowField, uploadOnce, type UploadStore } from '../src/upload/uploader'

const FIXTURES = path.resolve(__dirname, '..', '..', 'shared', 'fixtures')
const fixture = (name: string) => JSON.parse(readFileSync(path.join(FIXTURES, name), 'utf8'))

describe('shared contract (same fixtures as the API tests)', () => {
  it('accepts every valid fixture', () => {
    const names = readdirSync(FIXTURES).filter((f) => f.startsWith('batch-valid'))
    expect(names).toEqual(expect.arrayContaining(['batch-valid.json', 'batch-valid-channel.json', 'batch-valid-channel-unread.json', 'batch-valid-moved-on.json']))
    for (const n of names) expect(validateBatch(fixture(n)), n).toEqual({ ok: true })
  })
  it('rejects every invalid fixture', () => {
    const names = readdirSync(FIXTURES).filter((f) => f.startsWith('batch-invalid'))
    expect(names).toEqual(expect.arrayContaining(['batch-invalid-channel-long.json', 'batch-invalid-channel-null.json', 'batch-invalid-empty.json', 'batch-invalid-moved-on-date.json']))
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

function memoryStore(rows: CapturedRow[], meta: (id: string) => { startedAt: string; url?: string; channel?: string } = (id) => ({ startedAt: id, url: '/live/x' })): UploadStore & { uploaded: Set<string>; parked: Set<string> } {
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
    show: async (id) => meta(id),
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

  it('sends each line with the channel read when it was captured, never one the show learned later', async () => {
    const A = '2026-10-19T20:00:00.000Z'
    const B = '2026-10-18T20:00:00.000Z'
    const own = (id: string, seq: number): CapturedRow => ({ ...row(id, seq), channel: 'maple_cards_demo' })
    // The verifier's P1: the show's record names another seller (learned after the lines were
    // captured); the lines themselves were captured with the shop's name, or with none read.
    const rows = [own(A, 1), row(A, 2), own(A, 3), { ...row(A, 4), channel: 'x'.repeat(81) }, row(B, 1)]
    const store = memoryStore(rows, (id) => ({ startedAt: id, url: '/live/x', channel: 'birch_breaks_demo' }))
    const fetchFn = vi.fn(async () => ok())
    expect(await uploadOnce(store, settings, fetchFn)).toEqual({ status: 'sent', sent: 5, remaining: 0 })
    const bodies = fetchFn.mock.calls.map((c) => JSON.parse((c as unknown as [string, RequestInit])[1].body as string))
    // One batch per show and channel, each with its lines' own channel.
    expect(bodies.map((b) => [b.show.id, b.show.channel, b.rows.map((r: CapturedRow) => r.seq)])).toEqual([
      [A, 'maple_cards_demo', [1, 3]],
      [A, undefined, [2, 4]], // none read (or not a name: too long): sent with none, as before channels
      [B, undefined, [1]],
    ])
    expect(bodies.filter((b) => b.show.channel === undefined).every((b) => !('channel' in b.show))).toBe(true)
    // The contract has no channel on a line: it goes on the batch only.
    for (const b of bodies) expect(validateBatch(b)).toEqual({ ok: true })
  })

  it("sets aside only the lines the server refuses as another seller's; the shop's own lines go up", async () => {
    const A = '2026-10-19T20:00:00.000Z'
    const store = memoryStore([{ ...row(A, 1), channel: 'birch_breaks_demo' }, row(A, 2)])
    const fetchFn = vi.fn(async (_u: string, init: RequestInit) =>
      JSON.parse(String(init.body)).show.channel === 'birch_breaks_demo'
        ? new Response(JSON.stringify({ error: "These chat lines are from another seller's show" }), { status: 422 })
        : ok(),
    )
    const out = await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)
    expect(out).toMatchObject({ status: 'sent', sent: 1, remaining: 0, parked: 1 })
    expect([...store.parked]).toEqual([row(A, 1).id])
    expect([...store.uploaded]).toEqual([row(A, 2).id])
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

describe('the page moved on to another channel (the show ended)', () => {
  const A = '2026-11-19T20:00:00.000Z'
  const MOVED = '2026-11-19T22:41:07.000Z'

  /** `memoryStore` plus the moves the server is owed: one per show, after its lines. */
  function withMoves(rows: CapturedRow[], moves: Record<string, string>) {
    const store = memoryStore(rows, (id) => ({ startedAt: id, url: '/live/x', channel: 'maple_cards_demo' }))
    const sent: Record<string, string> = {}
    const pending = async (id: string) => (await store.pendingRows(10_000)).some((r) => r.showId === id)
    return Object.assign(store, {
      sent,
      unsentMoves: async () => {
        const out = []
        for (const [id, at] of Object.entries(moves)) {
          if (sent[id] === at || (await pending(id))) continue
          out.push({ id, startedAt: id, url: '/live/x', channel: 'maple_cards_demo', movedOnAt: at })
        }
        return out
      },
      markMoveSent: async (id: string, at: string) => void (sent[id] = at),
    })
  }

  const bodies = (fetchFn: ReturnType<typeof vi.fn>) => fetchFn.mock.calls.map((c) => JSON.parse(String((c as unknown as [string, RequestInit])[1].body)))

  it("is said once, in a batch with no lines, after the show's lines", async () => {
    const store = withMoves([row(A, 1), row(A, 2)], { [A]: MOVED })
    const fetchFn = vi.fn(async () => ok())
    const out = await uploadOnce(store, settings, fetchFn)
    expect(out).toEqual({ status: 'sent', sent: 2, remaining: 0, moved: 1 })
    const [lines, move] = bodies(fetchFn)
    expect(lines.rows).toHaveLength(2)
    expect('movedOnAt' in lines.show).toBe(false) // never on a batch of lines
    expect(move).toEqual({ version: 1, show: { id: A, startedAt: A, url: '/live/x', channel: 'maple_cards_demo', movedOnAt: MOVED }, rows: [] })
    expect(validateBatch(move)).toEqual({ ok: true })
    expect(store.sent[A]).toBe(MOVED)
    expect(await uploadOnce(store, settings, fetchFn)).toEqual({ status: 'idle' })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('waits while the show still has lines to send', async () => {
    const rows = Array.from({ length: BATCH_MAX + 5 }, (_, i) => row(A, i + 1))
    const store = withMoves(rows, { [A]: MOVED })
    const fetchFn = vi.fn(async () => ok())
    expect(await uploadOnce(store, settings, fetchFn)).toEqual({ status: 'sent', sent: BATCH_MAX, remaining: 5 })
    expect(bodies(fetchFn).every((b) => b.rows.length > 0)).toBe(true)
    expect(await uploadOnce(store, settings, fetchFn)).toEqual({ status: 'sent', sent: 5, remaining: 0, moved: 1 })
    expect(bodies(fetchFn).at(-1).rows).toEqual([])
  })

  it('a server that refuses it for good is not asked again; one it could not reach is', async () => {
    const store = withMoves([], { [A]: MOVED })
    const down = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    await expect(uploadOnce(store, settings, down)).rejects.toMatchObject({ kind: 'offline' })
    expect(store.sent[A]).toBeUndefined()
    // An API older than the field answers 400: not sent again, and no line is set aside.
    const old = vi.fn(async () => new Response(JSON.stringify({ error: 'invalid batch' }), { status: 400 }))
    expect(await uploadOnce(store, settings, old)).toEqual({ status: 'sent', sent: 0, remaining: 0, moved: 0 })
    expect(store.sent[A]).toBe(MOVED)
    expect(store.parked.size).toBe(0)
    expect(await uploadOnce(store, settings, old)).toEqual({ status: 'idle' })
  })
})

/** What the API from before channels answers to a show with a field it does not know (its validator's own words). */
const oldApiRefusal = () =>
  new Response(JSON.stringify({ error: 'invalid batch', details: ['show: fails additionalProperties False'] }), { status: 400 })
const sentBodies = (fetchFn: ReturnType<typeof vi.fn>) => fetchFn.mock.calls.map((c) => JSON.parse(String((c as unknown as [string, RequestInit])[1].body)))

describe('an API older than the extension (rolled back after a laptop installed the new build)', () => {
  const A = '2026-11-19T20:00:00.000Z'
  const own = (seq: number): CapturedRow => ({ ...row(A, seq), channel: 'maple_cards_demo' })

  it('sends the lines once more without the channel, and they are sent, not set aside', async () => {
    const store = memoryStore([own(1), own(2)])
    const fetchFn = vi.fn(async (_u: string, init: RequestInit) => ('channel' in JSON.parse(String(init.body)).show ? oldApiRefusal() : ok()))
    const out = await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)
    expect(out).toEqual({ status: 'sent', sent: 2, remaining: 0 })
    expect(store.parked.size).toBe(0)
    const [first, second] = sentBodies(fetchFn)
    expect(first.show.channel).toBe('maple_cards_demo')
    expect('channel' in second.show).toBe(false)
    expect(second.rows.map((r: CapturedRow) => r.seq)).toEqual([1, 2])
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('sets the lines aside when the retry is refused too, after exactly one retry', async () => {
    const store = memoryStore([own(1)])
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ error: 'invalid batch', details: ['rows/0/body: fails maxLength 4000'] }), { status: 400 }))
    const first = vi.fn(async (_u: string, init: RequestInit) => ('channel' in JSON.parse(String(init.body)).show ? oldApiRefusal() : fetchFn()))
    const out = await uploadOnce(store, settings, first as unknown as typeof fetch)
    expect(out).toMatchObject({ status: 'sent', sent: 0, parked: 1 })
    expect(first).toHaveBeenCalledTimes(2)
  })

  it("never resends another seller's lines (422): they are set aside with their channel", async () => {
    const store = memoryStore([own(1)])
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ error: "These chat lines are from another seller's show" }), { status: 422 }))
    const out = await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)
    expect(out).toMatchObject({ status: 'sent', sent: 0, parked: 1 })
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect([...store.parked]).toEqual([own(1).id])
  })

  it('does not strip anything for another 400, even on a batch with a channel', async () => {
    const store = memoryStore([own(1)])
    for (const details of [['rows/0/seq: fails minimum 0'], ['show/channel: fails maxLength 80'], ['show/url: fails additionalProperties False'], []]) {
      const fetchFn = vi.fn(async () => new Response(JSON.stringify({ error: 'invalid batch', details }), { status: 400 }))
      const s = memoryStore([own(1)])
      expect(await uploadOnce(s, settings, fetchFn as unknown as typeof fetch)).toMatchObject({ parked: 1 })
      expect(fetchFn).toHaveBeenCalledTimes(1)
    }
    expect(store.parked.size).toBe(0)
  })

  it('does not retry a batch that carries no channel (nothing to strip)', async () => {
    const store = memoryStore([row(A, 1)])
    const fetchFn = vi.fn(async () => oldApiRefusal())
    expect(await uploadOnce(store, settings, fetchFn as unknown as typeof fetch)).toMatchObject({ parked: 1 })
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it('a moved-on report the old API refuses stays owed (asked again at the next flush), never marked sent', async () => {
    const MOVED = '2026-11-19T22:41:07.000Z'
    const sent: Record<string, string> = {}
    const store = Object.assign(memoryStore([]), {
      unsentMoves: async () => (sent[A] ? [] : [{ id: A, startedAt: A, url: '/live/x', channel: 'maple_cards_demo', movedOnAt: MOVED }]),
      markMoveSent: async (id: string, at: string) => void (sent[id] = at),
    })
    const old = vi.fn(async () => oldApiRefusal())
    expect(await uploadOnce(store, settings, old as unknown as typeof fetch)).toEqual({ status: 'sent', sent: 0, remaining: 0, moved: 0 })
    expect(sent[A]).toBeUndefined()
    expect(old).toHaveBeenCalledTimes(1) // the report is sent as it is: stripping movedOnAt would make it an empty batch
    // The API is deployed forward again: the same move goes up and is marked.
    expect(await uploadOnce(store, settings, vi.fn(async () => ok()) as unknown as typeof fetch)).toEqual({ status: 'sent', sent: 0, remaining: 0, moved: 1 })
    expect(sent[A]).toBe(MOVED)
  })
})

describe('refusedUnknownShowField', () => {
  const rej = (msg: string, status = 400) => new UploadError('rejected', msg, undefined, status)
  it('matches the old validator\'s refusal of a show field, with or without its trailing value', () => {
    expect(refusedUnknownShowField(rej('invalid batch; show: fails additionalProperties False'))).toBe(true)
    expect(refusedUnknownShowField(rej('invalid batch; show: fails additionalProperties'))).toBe(true)
    expect(refusedUnknownShowField(rej('show: fails additionalProperties False; rows/0/seq: fails minimum 0'))).toBe(true)
  })
  it('does not match another path, another rule, another status or another kind of error', () => {
    for (const m of ['invalid batch', 'show/channel: fails maxLength 80', 'rows/0: fails additionalProperties False', 'show: fails required', 'xshow: fails additionalProperties False', 'show: fails additionalPropertiesX'])
      expect(refusedUnknownShowField(rej(m)), m).toBe(false)
    expect(refusedUnknownShowField(rej('show: fails additionalProperties False', 422))).toBe(false)
    expect(refusedUnknownShowField(rej('show: fails additionalProperties False', 413))).toBe(false)
    expect(refusedUnknownShowField(new UploadError('offline', 'show: fails additionalProperties False'))).toBe(false)
    expect(refusedUnknownShowField(new Error('show: fails additionalProperties False'))).toBe(false)
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
