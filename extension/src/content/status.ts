/**
 * What the panel says about the recording, in colours and words. Pure, so Vitest runs it.
 *
 * One rule the host can remember: green is working, amber sorts itself out, red needs a
 * click. Blue waits for the host's click on a chat message; grey is not recording.
 */
import { dollars, parseWinBody } from '../capture/player'
import type { CapturedRow } from '../capture/types'
import type { UploadStatus } from '../shared/messages'
import { type StockAlert, leftText } from '../stock/alerts'

export type DotState = 'idle' | 'picking' | 'good' | 'warn' | 'bad'

export interface Signals {
  /** Start was pressed; waiting for a click on a chat message */
  picking: boolean
  /** between Start and Stop */
  recording: boolean
  /** the Recorder is attached to the chat on this page */
  watching: boolean
  /** the background's last word on uploads; null before the first one */
  upload: UploadStatus | null
  /** chrome.* is gone: the extension was reloaded under this page */
  cutOff?: boolean
}

export function dotState(s: Signals): DotState {
  if (s.cutOff) return 'bad'
  if (s.picking) return 'picking'
  if (!s.recording) return 'idle'
  const u = s.upload
  if (u && (!u.configured || u.trouble === 'auth')) return 'bad'
  if (!s.watching || u?.trouble) return 'warn'
  return 'good'
}

/** The tail of the "saved N" line: how uploads are doing, in words (never a setting, a header or a vendor's text). */
export function uploadNote(u?: UploadStatus | null): string {
  if (!u) return ''
  if (!u.configured) return ' · not uploading (connect this laptop in Settings)'
  if (u.trouble === 'auth') return ` · ${u.pending} to upload — Sales Ops no longer recognises this laptop (reconnect in Settings)`
  if (u.trouble === 'offline') return ` · ${u.pending} to upload — can't reach Sales Ops, retrying`
  if (u.trouble === 'rejected') return (u.pending ? ` · ${u.pending} to upload` : '') + ` · ${u.parked} lines set aside (Sales Ops refused them)`
  if (u.lastError) return ` · ${u.pending} to upload — ${u.lastError}`
  return u.pending ? ` · ${u.pending} to upload` : ' · uploaded'
}

export type NoticeKind = 'win' | 'low' | 'out'

/** A notification beside the panel or the pill: five seconds, then gone. */
export interface Notice {
  kind: NoticeKind
  title: string
  detail: string
  /** red, not amber */
  urgent?: boolean
  /** notices Stop clears together */
  group?: 'stock'
}

/** A player row that is a win, as a notice; null for chat lines and declines. */
export function winNotice(row: CapturedRow): Notice | null {
  if (row.source !== 'player') return null
  const w = parseWinBody(row.body)
  if (!w) return null
  return { kind: 'win', title: `${w.winner} won · $${dollars(w.priceCents)}`, detail: w.title }
}

export function stockNotice(a: StockAlert): Notice {
  const out = a.item.available <= 0
  return {
    kind: out ? 'out' : 'low',
    title: out ? 'Sold out' : leftText(a.item.available),
    detail: `${a.item.name} · ${a.item.code}`,
    urgent: a.urgent,
    group: 'stock',
  }
}
