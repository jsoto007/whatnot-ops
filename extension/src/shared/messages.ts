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
  | { cmd: 'openOptions' }
  | { cmd: 'getUploadStatus' }
  | { cmd: 'flushUploads' }
  /** what the shop has 3 or fewer of (StockReply | null) */
  | { cmd: 'getStock' }
  /** extension pages only: the connection changed (paired, key saved, disconnected) */
  | { cmd: 'settingsChanged' }
  /** extension pages only: a different shop was connected; set unsent rows aside */
  | { cmd: 'parkPending' }

export interface UploadStatus {
  configured: boolean
  pending: number
  uploaded: number
  /** Rows set aside: rejected by the server, or captured for another shop. Never sent. */
  parked: number
  lastError?: string
  lastSentAt?: number
}

export type SessionReply = { show: ShowMeta; rows: CapturedRow[] } | null

/** background → content (via tabs.sendMessage) and → extension pages */
export type Broadcast = { cmd: 'toggle' } | { cmd: 'showUpdated'; showId: string }

export const RESUME_HOURS = 12
