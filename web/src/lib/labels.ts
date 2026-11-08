import type { OrderStatus, Role, SaleStatus } from './api'

/**
 * The words the shop reads for the server's codes. A page never prints a raw
 * code (`payment_failed`, `quick_hit`, a role value): it goes through one of
 * these, and an unknown code reads as a neutral word, not as itself.
 */

export const ROLE_LABEL: Record<Role, string> = {
  owner: 'Owner',
  manager: 'Manager',
  fulfillment: 'Fulfillment',
  viewer: 'Viewer',
}
export const roleLabel = (r: string | undefined | null): string => ROLE_LABEL[r as Role] ?? 'Team member'

export const SALE_STATUS_LABEL: Record<SaleStatus, string> = {
  detected: 'needs a decision',
  reserved: 'reserved',
  paid: 'paid',
  cancelled: 'cancelled',
  payment_failed: 'payment declined',
  fulfilled: 'shipped',
}
export const saleStatusLabel = (s: string): string => SALE_STATUS_LABEL[s as SaleStatus] ?? 'other'

/** Orders: `paid` means every item is paid, so the order is ready to pick. */
export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  open: 'needs review',
  paid: 'ready',
  picking: 'picking',
  packed: 'packed',
  shipped: 'shipped',
  cancelled: 'cancelled',
}
export const orderStatusLabel = (s: string): string => ORDER_STATUS_LABEL[s as OrderStatus] ?? 'other'

/** Lot types read from the listing title (api `rules.AUCTION_TYPES` / `LOT_LABELS`). */
const LOT_LABEL: Record<string, string> = { red: 'Red', mega: 'Mega', ultra: 'Ultra', bundle: 'Bundle', quick_hit: 'Quick Hit' }
export const lotLabel = (t: string | null | undefined): string => (t ? (LOT_LABEL[t] ?? '') : '')

/** What the stock-sheet import will do with a row. */
const IMPORT_ACTION: Record<string, string> = { create: 'new', set: 'update', unchanged: 'unchanged', skip: 'skip' }
export const importActionLabel = (a: string): string => IMPORT_ACTION[a] ?? 'skip'

/** What matching the payment file will do with a row. */
const RECONCILE_ACTION: Record<string, string> = {
  mark_paid: 'mark paid',
  cancel: 'cancel',
  decline: 'payment declined',
  unchanged: 'unchanged',
  unmatched: 'no matching sale',
  skip: 'skip',
  none: 'unchanged',
  uncovered: 'not in the file',
}
export const reconcileActionLabel = (a: string): string => RECONCILE_ACTION[a] ?? 'unchanged'

/** "3 update · 1 new" from a plan's {code: count} summary, in words. */
export const summaryWords = (summary: Record<string, number>, label: (code: string) => string): string =>
  Object.entries(summary)
    .filter(([, n]) => n)
    .map(([k, n]) => `${n} ${label(k)}`)
    .join(' · ')

/** Where a review item came from. */
export const reviewSourceLabel = (s: string): string => (s === 'ai' ? 'AI suggestion' : 'from the chat')
