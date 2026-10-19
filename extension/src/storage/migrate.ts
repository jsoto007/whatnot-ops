/**
 * One-time move from v1.2's chrome.storage.local layout
 * (cs_index, cs_show_<startedAt>, cs_session) into IndexedDB.
 * cs_watchwords stays in chrome.storage.local.
 */
import type { CapturedRow } from '../capture/types'
import { rowId } from '../capture/id'
import { KV_MIGRATED, KV_SESSION, kvGet, kvSet, putRows } from './db'

interface LegacyRow {
  time: string
  text: string
  ts?: number
}
interface LegacyShow {
  log: LegacyRow[]
  startedAt: string
  updatedAt: number
}

export const LEGACY_PREFIX = 'cs_show_'

export function legacyToRows(showId: string, log: LegacyRow[]): CapturedRow[] {
  return log.map((r, i) => {
    const ms = r.ts ?? Date.parse(showId) + i
    return {
      id: rowId(showId, '', r.text, ms),
      showId,
      username: '',
      body: r.text,
      rawText: r.text,
      capturedAt: new Date(ms).toISOString(),
      seq: i + 1,
    }
  })
}

export async function migrateLegacyStorage(
  storage: Pick<typeof chrome.storage.local, 'get' | 'remove'> = chrome.storage.local,
): Promise<{ shows: number; rows: number }> {
  if (await kvGet<boolean>(KV_MIGRATED)) return { shows: 0, rows: 0 }
  const all = (await storage.get(null)) as Record<string, unknown>
  let shows = 0
  let rows = 0
  const remove: string[] = []
  for (const [key, value] of Object.entries(all)) {
    if (!key.startsWith(LEGACY_PREFIX)) continue
    const s = value as LegacyShow
    if (!s?.log?.length || !s.startedAt) continue
    const converted = legacyToRows(s.startedAt, s.log)
    await putRows({ id: s.startedAt, startedAt: s.startedAt }, converted)
    shows++
    rows += converted.length
    remove.push(key)
  }
  const session = all['cs_session'] as (LegacyShow & { url?: string }) | undefined
  if (session?.startedAt && session.log?.length) {
    await kvSet(KV_SESSION, { showId: session.startedAt })
  }
  if ('cs_session' in all) remove.push('cs_session')
  if ('cs_index' in all) remove.push('cs_index')
  if (remove.length) await storage.remove(remove)
  await kvSet(KV_MIGRATED, true)
  return { shows, rows }
}
