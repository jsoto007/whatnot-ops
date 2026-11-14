import type { Settings } from '../upload/settings'
import { deviceHeaders } from '../upload/version'
import type { StockItem } from './alerts'

/**
 * What the show needs from this laptop, as the web app knows it: how the show sells, and
 * whether winners reach the app from Whatnot's own orders. When they do, the laptop only
 * records the chat, so the seller's own page (which shows no winners) is as good as any.
 */
export interface ShowNeeds {
  saleMode: 'winners_pick' | 'listing'
  winnersFromWhatnot: boolean
}

export interface StockReply {
  alertAt: number
  items: StockItem[]
  /** Absent from an older web app, and for a show it has not seen yet. */
  show?: ShowNeeds
}

const needsOf = (v: unknown): ShowNeeds | undefined => {
  const o = v as ShowNeeds | null
  if (!o || typeof o !== 'object' || typeof o.winnersFromWhatnot !== 'boolean') return undefined
  if (o.saleMode !== 'winners_pick' && o.saleMode !== 'listing') return undefined
  return { saleMode: o.saleMode, winnersFromWhatnot: o.winnersFromWhatnot }
}

const isItem = (i: unknown): i is StockItem => {
  const o = i as StockItem | null
  return !!o && typeof o.skuId === 'number' && typeof o.code === 'string' && typeof o.name === 'string' && typeof o.available === 'number'
}

/**
 * What is down to 3 or fewer. With `showId` (the recording's key) only what that show has
 * sold, so the host hears about tonight's items, not the whole catalog. Null when not
 * connected, offline or refused: the panel just shows nothing.
 */
export async function fetchStock(settings: Settings, showId?: string, fetchFn: typeof fetch = fetch): Promise<StockReply | null> {
  if (!settings.serverUrl || !settings.apiKey) return null
  const query = showId ? '?show=' + encodeURIComponent(showId) : ''
  try {
    const res = await fetchFn(settings.serverUrl + '/api/ingest/stock' + query, {
      redirect: 'error',
      headers: deviceHeaders(settings.apiKey),
    })
    if (!res.ok) return null
    const j = (await res.json()) as { alertAt?: unknown; items?: unknown; show?: unknown }
    if (typeof j.alertAt !== 'number' || !Array.isArray(j.items)) return null
    const show = needsOf(j.show)
    return { alertAt: j.alertAt, items: j.items.filter(isItem), ...(show ? { show } : {}) }
  } catch {
    return null
  }
}
