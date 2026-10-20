import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import type { CapturedRow } from '../src/capture/types'
import { validateBatch } from '../src/upload/schema'
import { normaliseUrl } from '../src/upload/settings'
import { BATCH_MAX, UploadError, backoffSeconds, postBatch, uploadOnce, type UploadStore } from '../src/upload/uploader'

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

function memoryStore(rows: CapturedRow[]): UploadStore & { uploaded: Set<string> } {
  const uploaded = new Set<string>()
  const pending = () => rows.filter((r) => !uploaded.has(r.id))
  return {
    uploaded,
    pendingRows: async (limit) => pending().slice(0, limit),
    pendingCount: async () => pending().length,
    markUploaded: async (ids) => ids.forEach((id) => uploaded.add(id)),
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
})

describe('helpers', () => {
  it('backs off 5s, 10s, 20s … capped at 5 minutes', () => {
    expect([1, 2, 3, 4, 10].map(backoffSeconds)).toEqual([5, 10, 20, 40, 300])
  })
  it('normalises the server url', () => {
    expect(normaliseUrl(' https://ops.test/// ')).toBe('https://ops.test')
  })
})
