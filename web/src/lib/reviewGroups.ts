import type { ReviewItem } from './api'

/** Route segment and group key for decisions that are not tied to a show. */
export const NO_SHOW = 'none'

export interface ShowGroup {
  /** The show's id as a string, or NO_SHOW. */
  key: string
  showId: number | null
  showDate: string | null
  items: ReviewItem[]
  /** How many decisions wait per reason, in the order they were first seen. */
  byReason: Map<string, number>
}

/** The open queue split by show, newest show first; decisions without a show come last. */
export function groupByShow(items: ReviewItem[]): ShowGroup[] {
  const groups = new Map<string, ShowGroup>()
  for (const it of items) {
    const key = it.showId == null ? NO_SHOW : String(it.showId)
    let g = groups.get(key)
    if (!g) {
      g = { key, showId: it.showId, showDate: it.showDate, items: [], byReason: new Map() }
      groups.set(key, g)
    }
    g.items.push(it)
    const under = reasonKey(it)
    g.byReason.set(under, (g.byReason.get(under) ?? 0) + 1)
  }
  return [...groups.values()].sort((a, b) => {
    if (a.showId == null) return 1
    if (b.showId == null) return -1
    return (b.showDate ?? '').localeCompare(a.showDate ?? '') || b.showId - a.showId
  })
}

/** What a decision is listed under: its reason, or for a win the pick worker has looked at,
 * what the worker found (a winner who did not pick is its own list). */
export function reasonKey(it: ReviewItem): string {
  const state = (it.proposal?.pick as { state?: string } | undefined)?.state
  if (it.reason !== 'unmatched_item' || !state || state === 'product') return it.reason
  return state === 'none' ? 'did_not_pick' : state === 'unread' || state === 'no_layout' ? 'pick_unread' : 'pick_unclear'
}
