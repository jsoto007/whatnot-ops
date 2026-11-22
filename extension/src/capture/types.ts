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
  /**
   * The channel (seller's handle) the page showed when this line was captured; absent when none
   * could be read (and on lines from before 1.10.0). Set by the content script at capture time,
   * never afterwards: the uploader sends each line with its own (upload/uploader.ts).
   */
  channel?: string
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
  /**
   * The channel (seller's handle) the recording is of, fixed only when the host chose the page
   * (Start or Pick chat on it, or the channel page's address they started at); never filled or
   * changed by a page read later (storage/db.ts). '' when that page showed none that could be
   * read; absent for shows recorded before 1.10.0. Lines carry their own (CapturedRow.channel).
   */
  channel?: string
  /**
   * When this laptop last saw the show's page move on to another channel (Whatnot does that when
   * a show ends), as an ISO time; the server is told once, after the show's lines
   * (upload/uploader.ts), and keeps its own clock's time of hearing it.
   */
  movedOnAt?: string
  /** The `movedOnAt` the server has (or refused for good): not sent again. */
  movedOnSent?: string
}
