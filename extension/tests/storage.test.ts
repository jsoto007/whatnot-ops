import { IDBFactory } from 'fake-indexeddb'
import {
  KV_SESSION,
  deleteShow,
  getRows,
  getShow,
  kvGet,
  listShows,
  markParked,
  markUploaded,
  pendingCount,
  pendingRows,
  pruneShows,
  putRows,
  resetDbForTests,
  uploadedCount,
} from '../src/storage/db'
import { legacyToRows, migrateLegacyStorage } from '../src/storage/migrate'
import type { CapturedRow } from '../src/capture/types'

function row(showId: string, seq: number, body = 'msg ' + seq): CapturedRow {
  return {
    id: `${showId}-${seq}`,
    showId,
    username: 'u',
    body,
    rawText: 'u ' + body,
    capturedAt: new Date(Date.parse(showId) + seq * 1000).toISOString(),
    seq,
  }
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
  resetDbForTests()
})

describe('db', () => {
  const show = { id: '2026-10-19T20:00:00.000Z', startedAt: '2026-10-19T20:00:00.000Z', url: '/live/x' }

  it('stores rows idempotently and keeps an accurate count', async () => {
    await putRows(show, [row(show.id, 1), row(show.id, 2)])
    const meta = await putRows(show, [row(show.id, 2), row(show.id, 3)]) // 2 re-sent
    expect(meta.count).toBe(3)
    expect(meta.date).toBe('2026-10-19')
    expect(meta.url).toBe('/live/x')
    expect((await getRows(show.id)).map((r) => r.seq)).toEqual([1, 2, 3])
  })

  it('returns rows in seq order even when inserted out of order', async () => {
    await putRows(show, [row(show.id, 3), row(show.id, 1), row(show.id, 2)])
    expect((await getRows(show.id)).map((r) => r.seq)).toEqual([1, 2, 3])
  })

  it('lists shows newest first and deletes a show with its rows', async () => {
    const older = { id: '2026-10-18T20:00:00.000Z', startedAt: '2026-10-18T20:00:00.000Z' }
    await putRows(older, [row(older.id, 1)])
    await putRows(show, [row(show.id, 1)])
    expect((await listShows()).map((s) => s.id)).toEqual([show.id, older.id])
    await deleteShow(older.id)
    expect(await getShow(older.id)).toBeUndefined()
    expect(await getRows(older.id)).toEqual([])
    expect((await getRows(show.id)).length).toBe(1)
  })

  it('prunes the oldest shows beyond the limit', async () => {
    for (let d = 1; d <= 5; d++) {
      const id = `2026-10-0${d}T20:00:00.000Z`
      await putRows({ id, startedAt: id }, [row(id, 1)])
      await markUploaded([`${id}-1`])
    }
    const dropped = await pruneShows(3)
    expect(dropped).toEqual(['2026-10-02T20:00:00.000Z', '2026-10-01T20:00:00.000Z'])
    expect((await listShows()).length).toBe(3)
  })

  it('never prunes a show whose rows have not been uploaded', async () => {
    const old = { id: '2026-09-01T20:00:00.000Z', startedAt: '2026-09-01T20:00:00.000Z' }
    await putRows(old, [row(old.id, 1), row(old.id, 2)])
    await markUploaded([`${old.id}-1`]) // one sent, one still waiting
    // Thirty newer shows, as empty Starts leave them: each one counts toward the limit.
    for (let d = 1; d <= 30; d++) {
      const id = `2026-10-${String(d).padStart(2, '0')}T20:00:00.000Z`
      await putRows({ id, startedAt: id }, [])
    }
    expect(await pruneShows()).toEqual([])
    expect((await getRows(old.id)).length).toBe(2)
    expect(await pendingCount()).toBe(1)

    // Once its last row is sent, it is an ordinary old show again.
    await markUploaded([`${old.id}-2`])
    expect(await pruneShows()).toEqual([old.id])
    expect(await getShow(old.id)).toBeUndefined()
  })

  it('prunes a show whose waiting rows were set aside', async () => {
    const old = { id: '2026-09-01T20:00:00.000Z', startedAt: '2026-09-01T20:00:00.000Z' }
    await putRows(old, [row(old.id, 1)])
    await markParked([`${old.id}-1`])
    const newer = { id: '2026-10-01T20:00:00.000Z', startedAt: '2026-10-01T20:00:00.000Z' }
    await putRows(newer, [row(newer.id, 1)]) // pending, but within the limit
    expect(await pruneShows(1)).toEqual([old.id])
    expect(await pendingCount()).toBe(1)
  })
})

describe('legacy migration', () => {
  it('converts v1.2 rows keeping text and order', () => {
    const rows = legacyToRows('2026-10-01T01:00:00.000Z', [
      { time: '8:00:01 PM', text: 'lani808 | mine', ts: 1_700_000_000_000 },
      { time: '8:00:02 PM', text: 'sold' },
    ])
    expect(rows.map((r) => r.seq)).toEqual([1, 2])
    expect(rows[0]).toMatchObject({ username: '', body: 'lani808 | mine', rawText: 'lani808 | mine' })
    expect(rows[0]!.capturedAt).toBe(new Date(1_700_000_000_000).toISOString())
    expect(rows[0]!.id).not.toBe(rows[1]!.id)
  })

  it('moves cs_show_* and cs_session into IndexedDB once, removing the old keys', async () => {
    const startedAt = '2026-10-01T01:00:00.000Z'
    const store: Record<string, unknown> = {
      cs_index: [{ id: 'cs_show_' + startedAt }],
      ['cs_show_' + startedAt]: { startedAt, updatedAt: 1, log: [{ time: 't', text: 'a' }, { time: 't', text: 'b' }] },
      cs_session: { startedAt, updatedAt: 1, log: [{ time: 't', text: 'a' }] },
      cs_watchwords: ['mine'],
    }
    const removed: string[] = []
    const fake = {
      get: async () => store,
      remove: async (keys: string | string[]) => {
        removed.push(...(Array.isArray(keys) ? keys : [keys]))
      },
    } as unknown as typeof chrome.storage.local

    const first = await migrateLegacyStorage(fake)
    expect(first).toEqual({ shows: 1, rows: 2 })
    expect((await getRows(startedAt)).map((r) => r.body)).toEqual(['a', 'b'])
    expect(await kvGet(KV_SESSION)).toEqual({ showId: startedAt })
    expect(removed.sort()).toEqual(['cs_index', 'cs_session', 'cs_show_' + startedAt].sort())
    expect(removed).not.toContain('cs_watchwords')

    const second = await migrateLegacyStorage(fake)
    expect(second).toEqual({ shows: 0, rows: 0 })
  })
})

describe('upload outbox', () => {
  const show = { id: '2026-10-19T20:00:00.000Z', startedAt: '2026-10-19T20:00:00.000Z' }

  it('new rows are pending until marked uploaded, oldest first', async () => {
    await putRows(show, [row(show.id, 2), row(show.id, 1), row(show.id, 3)])
    expect(await pendingCount()).toBe(3)
    expect((await pendingRows(2)).map((r) => r.seq)).toEqual([1, 2])
    await markUploaded([`${show.id}-1`, `${show.id}-2`])
    expect(await pendingCount()).toBe(1)
    expect(await uploadedCount()).toBe(2)
    expect((await pendingRows(10)).map((r) => r.seq)).toEqual([3])
    // re-adding an uploaded row does not make it pending again
    await putRows(show, [row(show.id, 1)])
    expect(await pendingCount()).toBe(1)
    // getRows hides the flag
    expect(Object.keys((await getRows(show.id))[0]!)).not.toContain('uploaded')
  })
})
