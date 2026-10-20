import type { CapturedRow } from '../capture/types'

/** The server's limits (shared/chat-event-batch.schema.json). Rows are cut to fit
 * before they are stored or sent, so one huge chat line can never make the
 * server reject a batch and stall the upload queue. */
export const LIMITS = { showId: 64, username: 80, body: 4000, rawText: 4000, url: 512 } as const

const cut = (s: string, n: number) => (s.length > n ? s.slice(0, n) : s)

export function clampRow(r: CapturedRow): CapturedRow {
  return {
    ...r,
    showId: cut(r.showId, LIMITS.showId),
    username: cut(r.username ?? '', LIMITS.username),
    body: cut(r.body ?? '', LIMITS.body),
    rawText: cut(r.rawText || r.body || '?', LIMITS.rawText),
  }
}

export const clampUrl = (u: string | undefined): string | undefined => (u && u.length <= LIMITS.url ? u : undefined)
