import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { CapturedRow, ShowMeta } from '../capture/types'
import type { ChatSpot, ShowRef } from '../shared/messages'
import { showDate } from '../shared/showDate'

export const DB_NAME = 'chat-scraper'
export const MAX_SHOWS = 30

/**
 * A row as stored. `uploaded`: 0 pending, 1 accepted by the API, 2 set aside
 * (the server rejected its batch, or it was captured for a different shop than
 * the one this laptop is connected to now). IndexedDB cannot index booleans.
 */
export type StoredRow = CapturedRow & { uploaded: 0 | 1 | 2 }

interface CsDB extends DBSchema {
  shows: { key: string; value: ShowMeta; indexes: { 'by-started': string } }
  rows: {
    key: string
    value: StoredRow
    indexes: { 'by-show-seq': [string, number]; 'by-uploaded': [number, string, number] }
  }
  kv: { key: string; value: unknown }
}

export const DB_VERSION = 2
let dbp: Promise<IDBPDatabase<CsDB>> | null = null

export function getDb(): Promise<IDBPDatabase<CsDB>> {
  dbp ??= openDB<CsDB>(DB_NAME, DB_VERSION, {
    async upgrade(db, oldVersion, _new, tx) {
      if (oldVersion < 1) {
        const shows = db.createObjectStore('shows', { keyPath: 'id' })
        shows.createIndex('by-started', 'startedAt')
        const rows = db.createObjectStore('rows', { keyPath: 'id' })
        rows.createIndex('by-show-seq', ['showId', 'seq'])
        db.createObjectStore('kv')
      }
      if (oldVersion < 2) {
        const rows = tx.objectStore('rows')
        rows.createIndex('by-uploaded', ['uploaded', 'showId', 'seq'])
        // Rows saved before uploads existed are pending.
        let cur = await rows.openCursor()
        while (cur) {
          if (cur.value.uploaded === undefined) await cur.update({ ...cur.value, uploaded: 0 })
          cur = await cur.continue()
        }
      }
    },
  })
  return dbp
}

/** Test hook: forget the cached connection (fake-indexeddb is reset between tests). */
export function resetDbForTests(): void {
  dbp = null
}

const showRange = (showId: string) => IDBKeyRange.bound([showId, 0], [showId, Number.MAX_SAFE_INTEGER])

/** Add rows (idempotent by id) and refresh the show's metadata. Returns the new metadata. */
export async function putRows(show: ShowRef, rows: CapturedRow[]): Promise<ShowMeta> {
  const db = await getDb()
  const tx = db.transaction(['rows', 'shows'], 'readwrite')
  const rowStore = tx.objectStore('rows')
  for (const r of rows) {
    if ((await rowStore.getKey(r.id)) === undefined) await rowStore.add({ ...r, uploaded: 0 })
  }
  const count = await rowStore.index('by-show-seq').count(showRange(show.id))
  const prev = await tx.objectStore('shows').get(show.id)
  const meta: ShowMeta = {
    id: show.id,
    startedAt: show.startedAt,
    updatedAt: Date.now(),
    count,
    date: showDate(show.startedAt),
    url: show.url ?? prev?.url,
  }
  await tx.objectStore('shows').put(meta)
  await tx.done
  return meta
}

export async function getRows(showId: string): Promise<CapturedRow[]> {
  const db = await getDb()
  const stored = await db.getAllFromIndex('rows', 'by-show-seq', showRange(showId))
  return stored.map(({ uploaded: _u, ...row }) => row)
}

// ---------- upload outbox ----------
const pendingRange = IDBKeyRange.bound([0, '', 0], [0, '\uffff', Number.MAX_SAFE_INTEGER])

/** Oldest pending rows first (by show, then seq), up to `limit`. */
export async function pendingRows(limit: number): Promise<CapturedRow[]> {
  const db = await getDb()
  const out: CapturedRow[] = []
  let cur = await db.transaction('rows').store.index('by-uploaded').openCursor(pendingRange)
  while (cur && out.length < limit) {
    const { uploaded: _u, ...row } = cur.value
    out.push(row)
    cur = await cur.continue()
  }
  return out
}

export async function pendingCount(): Promise<number> {
  return (await getDb()).countFromIndex('rows', 'by-uploaded', pendingRange)
}

export async function uploadedCount(): Promise<number> {
  const db = await getDb()
  return db.countFromIndex('rows', 'by-uploaded', IDBKeyRange.bound([1, '', 0], [1, '\uffff', Number.MAX_SAFE_INTEGER]))
}

export async function markUploaded(ids: string[]): Promise<void> {
  const db = await getDb()
  const tx = db.transaction('rows', 'readwrite')
  for (const id of ids) {
    const r = await tx.store.get(id)
    if (r && !r.uploaded) await tx.store.put({ ...r, uploaded: 1 })
  }
  await tx.done
}

/** Never retried: their batch was rejected, so they must not block the queue. */
export async function markParked(ids: string[]): Promise<void> {
  const db = await getDb()
  const tx = db.transaction('rows', 'readwrite')
  for (const id of ids) {
    const r = await tx.store.get(id)
    if (r && r.uploaded === 0) await tx.store.put({ ...r, uploaded: 2 })
  }
  await tx.done
}

/** Set every pending row aside (the laptop moved to another shop). Returns how many. */
export async function parkAllPending(): Promise<number> {
  const db = await getDb()
  const tx = db.transaction('rows', 'readwrite')
  let n = 0
  let cur = await tx.store.index('by-uploaded').openCursor(pendingRange)
  while (cur) {
    await cur.update({ ...cur.value, uploaded: 2 })
    n++
    cur = await cur.continue()
  }
  await tx.done
  return n
}

export async function parkedCount(): Promise<number> {
  const db = await getDb()
  return db.countFromIndex('rows', 'by-uploaded', IDBKeyRange.bound([2, '', 0], [2, '\uffff', Number.MAX_SAFE_INTEGER]))
}

export async function getShow(showId: string): Promise<ShowMeta | undefined> {
  return (await getDb()).get('shows', showId)
}

/** Newest first. */
export async function listShows(): Promise<ShowMeta[]> {
  const all = await (await getDb()).getAll('shows')
  return all.sort((a, b) => b.startedAt.localeCompare(a.startedAt))
}

export async function deleteShow(showId: string): Promise<void> {
  const db = await getDb()
  const tx = db.transaction(['rows', 'shows'], 'readwrite')
  let cur = await tx.objectStore('rows').index('by-show-seq').openCursor(showRange(showId))
  while (cur) {
    await cur.delete()
    cur = await cur.continue()
  }
  await tx.objectStore('shows').delete(showId)
  await tx.done
}

/** Drop the oldest shows beyond MAX_SHOWS. Returns the ids removed. */
export async function pruneShows(max = MAX_SHOWS): Promise<string[]> {
  const shows = await listShows()
  const drop = shows.slice(max).map((s) => s.id)
  for (const id of drop) await deleteShow(id)
  return drop
}

export async function kvGet<T>(key: string): Promise<T | undefined> {
  return (await (await getDb()).get('kv', key)) as T | undefined
}

export async function kvSet(key: string, value: unknown): Promise<void> {
  await (await getDb()).put('kv', value, key)
}

export async function kvDelete(key: string): Promise<void> {
  await (await getDb()).delete('kv', key)
}

export const KV_SESSION = 'session'
export const KV_RECORDING = 'recording'
export const KV_MIGRATED = 'migrated_v1'

export interface SessionRef {
  showId: string
}

/** A show that is recording until someone presses Stop, and the tab doing it. */
export interface RecordingRef {
  showId: string
  spot: ChatSpot
  tabId?: number
}
