export const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
export const fmtLongDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
/** The shop's zone (America/Chicago): show times read the same on every laptop. */
export const SHOP_TZ = 'America/Chicago'
export const fmtShopTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit', timeZone: SHOP_TZ })
export const fmtShopClock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ })
/** "10-31-2026": how the shop's order slips write the auction date. */
export const sheetDate = (iso: string) => {
  const [y, m, d] = iso.split('-')
  return `${m}-${d}-${y}`
}
export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
/** "just now", "12 min ago", "3 h ago", then a date and time. */
export function fmtWhen(iso: string, now = Date.now()) {
  const t = new Date(iso)
  const mins = Math.round((now - t.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`
  return t.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
/** An id for one submit of a form, so a double click or a retry is applied once. */
export function requestId() {
  const c = globalThis.crypto
  return c && 'randomUUID' in c ? c.randomUUID().replace(/-/g, '') : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`
}
