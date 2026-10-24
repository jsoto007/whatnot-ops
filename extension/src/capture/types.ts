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
  /**
   * Set only on a win read from Whatnot's video player (see player.ts). Chat rows have
   * no source. Viewers can type in chat but not in the player, so the server lets only
   * player rows say who won what for how much.
   */
  source?: 'player'
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
