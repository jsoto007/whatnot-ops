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

export interface UploadStatus {
  configured: boolean
  pending: number
  uploaded: number
  lastError?: string
  lastSentAt?: number
}

export type SessionReply = { show: ShowMeta; rows: CapturedRow[] } | null

/** background → content (via tabs.sendMessage) and → extension pages */
export type Broadcast = { cmd: 'toggle' } | { cmd: 'showUpdated'; showId: string }

export const RESUME_HOURS = 12
