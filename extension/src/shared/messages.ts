import type { CapturedRow, ShowMeta } from '../capture/types'

export interface ShowRef {
  id: string
  startedAt: string
  url?: string
}

/** content → background */
export type Request =
  | { cmd: 'rows'; show: ShowRef; rows: CapturedRow[] }
  | { cmd: 'getSession' }
  | { cmd: 'clearSession' }
  | { cmd: 'openSearch' }

export type SessionReply = { show: ShowMeta; rows: CapturedRow[] } | null

/** background → content (via tabs.sendMessage) and → extension pages */
export type Broadcast = { cmd: 'toggle' } | { cmd: 'showUpdated'; showId: string }

export const RESUME_HOURS = 12
