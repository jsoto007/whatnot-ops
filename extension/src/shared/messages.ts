import type { CapturedRow, ShowMeta } from '../capture/types'
import type { ShipmentRow } from '../shipments/read'

export interface ShowRef {
  id: string
  startedAt: string
  url?: string
}

/**
 * Where a recording reads the chat: the row selectors it has used, newest first
 * (the host's pick, then any it healed to), and the page it was on. Saved while
 * recording, so a reloaded page carries on without a click; cleared by Stop.
 */
export interface ChatSpot {
  selectors: string[]
  /** `showPage(location.pathname)` when it started */
  page: string
}

export const MAX_SELECTORS = 3

/** content → background */
export type Request =
  | { cmd: 'rows'; show: ShowRef; rows: CapturedRow[] }
  /** this tab records `show` from `spot` until Stop (spot null) */
  | { cmd: 'recording'; show: ShowRef; spot: ChatSpot | null }
  | { cmd: 'getSession' }
  | { cmd: 'clearSession' }
  | { cmd: 'openSearch' }
  | { cmd: 'openOptions' }
  | { cmd: 'getUploadStatus' }
  | { cmd: 'flushUploads' }
  /** what this show has sold that is down to 3 or fewer (StockReply | null) */
  | { cmd: 'getStock'; showId?: string }
  /** what the shop knows about the people who just won, before this show (BuyersReply | null) */
  | { cmd: 'getBuyers'; showId?: string; names: string[] }
  /** extension pages only: the connection changed (paired, key saved, disconnected) */
  | { cmd: 'settingsChanged' }
  /** extension pages only: a different shop was connected; set unsent rows aside */
  | { cmd: 'parkPending' }
  /** rows read off Seller Hub's Shipments table → ShipmentsReply | { error } */
  | { cmd: 'shipments'; rows: ShipmentRow[]; show: string }

/**
 * The shop's web app → background (chrome.runtime.sendMessage(id, …) via
 * externally_connectable; only the portal's origin, which its download writes
 * into the manifest). `reload` makes Chrome load this folder's files again,
 * after they were replaced with a new download.
 */
export type PortalRequest = { cmd: 'hello' } | { cmd: 'reload' }

export interface PortalHello {
  /** the version running now */
  version: string
  /** the version of the files in the folder: ahead of `version` once a new download was copied in */
  filesVersion: string | null
  /** a show is recording (it carries on by itself after a reload) */
  recording: boolean
}

/** portal.js → the web app page (window.postMessage): Chat Scraper runs on this laptop. */
export interface PortalHere {
  source: 'chat-scraper'
  type: 'here'
  id: string
  version: string
}

export interface UploadStatus {
  configured: boolean
  pending: number
  /** Only for the extension's own pages (Settings): counting it walks every stored row. */
  uploaded?: number
  /** Rows set aside: rejected by the server, or captured for another shop. Never sent. */
  parked: number
  lastError?: string
  /** Why the last upload failed, for the panel's colours: the key was refused, the server could not be reached, or lines were set aside. */
  trouble?: 'auth' | 'offline' | 'rejected'
  lastSentAt?: number
}

/**
 * `recording`: the show was still recording (no Stop) and this tab may carry on.
 * `elsewhere`: it is still recording in another open tab.
 */
export type SessionReply = { show: ShowMeta; rows: CapturedRow[]; recording?: ChatSpot; elsewhere?: boolean } | null

/** background → content (via tabs.sendMessage) and → extension pages */
export type Broadcast =
  | { cmd: 'toggle' }
  | { cmd: 'showUpdated'; showId: string }
  /** is this tab recording right now? → PingReply */
  | { cmd: 'ping' }
  /** another tab took over recording this show */
  | { cmd: 'released'; showId: string }

export interface PingReply {
  recording: boolean
}

/** The part of a Whatnot path that names the show: `/live/<id>` of `/live/<id>/…`. */
export function showPage(pathname: string): string {
  return '/' + pathname.split('/').filter(Boolean).slice(0, 2).join('/')
}

export const RESUME_HOURS = 12
