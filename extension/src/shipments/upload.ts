import type { Settings } from '../upload/settings'
import { deviceHeaders } from '../upload/version'
import type { ShipmentRow } from './read'

/** The server refuses more than this per read (api/app/services/shipments.py MAX_ROWS). */
export const MAX_ROWS = 2000

export interface ShipmentsReply {
  stored: number
  skipped: number
  unmatched: number
  /** web app show id → rows matched to it */
  shows: Record<string, number>
}

const str = (v: unknown, n: number) => (typeof v === 'string' ? v.slice(0, n) : '')

/** A content-script message, cut to the server's limits; null when it is not one. */
export function cleanShipments(rows: unknown): ShipmentRow[] | null {
  if (!Array.isArray(rows) || rows.length > MAX_ROWS) return null
  return rows
    .filter((r): r is Record<string, unknown> => !!r && typeof r === 'object')
    .map((r) => ({
      recipient: str(r.recipient, 80),
      orderDate: str(r.orderDate, 80),
      items: str(r.items, 1000),
      itemLines: Array.isArray(r.itemLines) ? r.itemLines.slice(0, 60).map((x) => str(x, 240)) : [],
      value: str(r.value, 40),
      status: str(r.status, 80),
      tracking: str(r.tracking, 120),
    }))
    .filter((r) => r.recipient)
}

/** Send one read of the Shipments table. The key stays in the background. */
export async function postShipments(settings: Settings, rows: ShipmentRow[], fetchFn: typeof fetch = fetch): Promise<ShipmentsReply | { error: string }> {
  if (!settings.serverUrl || !settings.apiKey) return { error: 'This laptop is not connected to the web app (Settings).' }
  try {
    const res = await fetchFn(settings.serverUrl + '/api/ingest/shipments', {
      method: 'POST',
      redirect: 'error',
      headers: { ...deviceHeaders(settings.apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows }),
    })
    const j = (await res.json().catch(() => ({}))) as Partial<ShipmentsReply> & { error?: string }
    if (!res.ok) return { error: j.error ?? `The web app said ${res.status}.` }
    if (typeof j.stored !== 'number') return { error: 'Unexpected answer from the web app.' }
    return { stored: j.stored, skipped: j.skipped ?? 0, unmatched: j.unmatched ?? 0, shows: j.shows ?? {} }
  } catch {
    return { error: 'Could not reach the web app.' }
  }
}
