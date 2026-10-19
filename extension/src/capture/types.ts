/** One captured chat line. `id` is stable across retries (see id.ts). */
export interface CapturedRow {
  id: string
  showId: string
  username: string
  body: string
  rawText: string
  /** ISO-8601 UTC */
  capturedAt: string
  /** 1-based order within the show on this device */
  seq: number
}

export interface ShowMeta {
  /** ISO start time; doubles as the id */
  id: string
  startedAt: string
  updatedAt: number
  count: number
  /** YYYY-MM-DD show date in the shop's zone (see showDate.ts) */
  date: string
  url?: string
}
