import type { Settings } from '../upload/settings'
import { deviceHeaders } from '../upload/version'
import type { ShipmentLot, ShipmentRow } from './read'

/** The server refuses more than this per read (api/app/services/shipments.py MAX_ROWS). */
export const MAX_ROWS = 2000

export interface ShipmentsReply {
  stored: number
  /** Lots (Whatnot orders) stored. */
  lots: number
  /** The web app's show the read was filed under, when one matched Whatnot's show filter. */
  show: { id: number; date: string } | null
  skipped: number
  unmatched: number
  /** web app show id → rows matched to it */
  shows: Record<string, number>
}

const str = (v: unknown, n: number) => (typeof v === 'string' ? v.slice(0, n) : '')
const MAX_LOTS = 200

/** Whatnot's show filter label as sent to the server. */
export const cleanShowLabel = (v: unknown): string => str(v, 300)

function cleanLots(v: unknown): ShipmentLot[] {
  if (!Array.isArray(v)) return []
  return v
    .slice(0, MAX_LOTS)
    .filter((l): l is Record<string, unknown> => !!l && typeof l === 'object')
    .map((l) => ({
      title: str(l.title, 240),
      orderId: /^\d{1,20}$/.test(str(l.orderId, 20)) ? str(l.orderId, 20) : '',
      quantity: str(l.quantity, 80),
      price: str(l.price, 80),
      shipping: str(l.shipping, 80),
      saleType: str(l.saleType, 80),
    }))
    .filter((l) => l.title || l.orderId)
}

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
      lots: cleanLots(r.lots),
    }))
    .filter((r) => r.recipient)
}

/** Send one read of the Shipments table. The key stays in the background. */
export async function postShipments(settings: Settings, rows: ShipmentRow[], show: string, fetchFn: typeof fetch = fetch): Promise<ShipmentsReply | { error: string }> {
  if (!settings.serverUrl || !settings.apiKey) return { error: 'This laptop is not connected to the web app (Settings).' }
  try {
    const res = await fetchFn(settings.serverUrl + '/api/ingest/shipments', {
      method: 'POST',
      redirect: 'error',
      headers: { ...deviceHeaders(settings.apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({ show, rows }),
    })
    const j = (await res.json().catch(() => ({}))) as Partial<ShipmentsReply> & { error?: string }
    if (!res.ok) return { error: j.error ?? `The web app said ${res.status}.` }
    if (typeof j.stored !== 'number') return { error: 'Unexpected answer from the web app.' }
    return { stored: j.stored, lots: j.lots ?? 0, show: j.show ?? null, skipped: j.skipped ?? 0, unmatched: j.unmatched ?? 0, shows: j.shows ?? {} }
  } catch {
    return { error: 'Could not reach the web app.' }
  }
}
