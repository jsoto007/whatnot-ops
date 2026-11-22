import type { CapturedRow } from '../capture/types'

/** The server's limits (shared/chat-event-batch.schema.json). Rows are cut to fit
 * before they are stored or sent, so one huge chat line can never make the
 * server reject a batch and stall the upload queue. */
export const LIMITS = { showId: 64, username: 80, body: 4000, rawText: 4000, url: 512, channel: 80 } as const

const cut = (s: string, n: number) => (s.length > n ? s.slice(0, n) : s)

export function clampRow(r: CapturedRow): CapturedRow {
  const { source, channel, ...rest } = r
  const name = clampChannel(channel)
  return {
    ...rest,
    showId: cut(r.showId, LIMITS.showId),
    username: cut(r.username ?? '', LIMITS.username),
    body: cut(r.body ?? '', LIMITS.body),
    rawText: cut(r.rawText || r.body || '?', LIMITS.rawText),
    // Only the exact marker survives; chat rows go out without the field, as before.
    ...(source === 'player' ? { source } : {}),
    // The channel read when the line was captured, kept only when it is a name the server takes.
    ...(name ? { channel: name } : {}),
  }
}

/**
 * A row as the server takes it. The contract has no channel on a line: the uploader sends each
 * line's own channel on its batch (upload/uploader.ts), one batch per show and channel.
 */
export function wireRow(r: CapturedRow): CapturedRow {
  const { channel: _channel, ...row } = clampRow(r)
  return row
}

export const clampUrl = (u: string | undefined): string | undefined => (u && u.length <= LIMITS.url ? u : undefined)

/** A time the content script says the page moved on: an ISO date-time the server will take, else nothing. */
export const clampMovedOnAt = (t: unknown): string | undefined =>
  typeof t === 'string' && t.length <= 40 && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/.test(t) && !Number.isNaN(Date.parse(t)) ? t : undefined

/** A channel name as the server takes it: a string within its limit ('' = not read), else nothing. */
export const clampChannel = (c: unknown): string | undefined => (typeof c === 'string' && c.length <= LIMITS.channel ? c : undefined)
