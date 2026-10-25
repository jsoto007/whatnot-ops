import type { Settings } from '../upload/settings'
import type { StockItem } from './alerts'

export interface StockReply {
  alertAt: number
  items: StockItem[]
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
      headers: { Authorization: 'Bearer ' + settings.apiKey },
    })
    if (!res.ok) return null
    const j = (await res.json()) as { alertAt?: unknown; items?: unknown }
    if (typeof j.alertAt !== 'number' || !Array.isArray(j.items)) return null
    return { alertAt: j.alertAt, items: j.items.filter(isItem) }
  } catch {
    return null
  }
}
