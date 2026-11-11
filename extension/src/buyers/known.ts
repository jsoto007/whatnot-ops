import type { Settings } from '../upload/settings'
import { deviceHeaders } from '../upload/version'

/** What the shop knows about someone who just won, before tonight. */
export interface BuyerSeen {
  name: string
  /** the team's flag: someone to look after, or to keep an eye on */
  flag: 'vip' | 'watch' | null
  /** nothing of theirs stood before tonight's show */
  firstTime: boolean
  shows: number
  items: number
}

export interface BuyersReply {
  buyers: BuyerSeen[]
}

/** Names one lookup may carry; the panel asks about one winner at a time. */
export const MAX_NAMES = 20
export const NAME_MAX = 80

const isBuyer = (b: unknown): b is BuyerSeen => {
  const o = b as BuyerSeen | null
  return (
    !!o &&
    typeof o.name === 'string' &&
    (o.flag === null || o.flag === 'vip' || o.flag === 'watch') &&
    typeof o.firstTime === 'boolean' &&
    typeof o.shows === 'number' &&
    typeof o.items === 'number'
  )
}

/**
 * Ask the web app about the people who just won (`showId` is the recording's key, so
 * "before tonight" means before this show). Null when not connected, offline, refused, or
 * the web app is older than this: the panel then says nothing about the buyer.
 */
export async function fetchBuyers(settings: Settings, showId: string | undefined, names: string[], fetchFn: typeof fetch = fetch): Promise<BuyersReply | null> {
  if (!settings.serverUrl || !settings.apiKey || !names.length) return null
  try {
    const res = await fetchFn(settings.serverUrl + '/api/ingest/buyers', {
      method: 'POST',
      redirect: 'error',
      headers: { ...deviceHeaders(settings.apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...(showId ? { show: showId } : {}), names }),
    })
    if (!res.ok) return null
    const j = (await res.json()) as { buyers?: unknown }
    if (!Array.isArray(j.buyers)) return null
    return { buyers: j.buyers.filter(isBuyer) }
  } catch {
    return null
  }
}

const FLAG: Record<'vip' | 'watch', string> = { vip: 'VIP', watch: 'Watch list' }
const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** "New buyer", "Back again · 12 items over 5 shows", with the team's flag first. */
export function buyerLine(b: BuyerSeen): string {
  const history = b.firstTime ? 'New buyer' : `Back again · ${count(b.items, 'item', 'items')} over ${count(b.shows, 'show', 'shows')}`
  return b.flag ? `${FLAG[b.flag]} · ${history.charAt(0).toLowerCase()}${history.slice(1)}` : history
}
