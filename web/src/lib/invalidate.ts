import type { QueryClient } from '@tanstack/react-query'

/** Everything a sale, order or stock change can alter. Match is on the first key segment, so every show / order id is covered. */
const SALES_KEYS = new Set([
  'shows', 'show', 'show-dashboard', 'sales', 'orders', 'order', 'buyer', 'buyers', 'pick-list', 'order-slips', 'shipping',
  'whatnot-orders', 'review', 'giveaways', 'shelves', 'inventory', 'inventory-activity', 'movements', 'products', 'dashboard',
  'nav-counts', 'events', 'messages', 'whatnot-catalog', 'whatnot-ledger', 'whatnot-shows', 'audit', 'buyer-flags',
])

/**
 * Refetch the pages a sales / orders / stock action can change, and nothing else (not the team,
 * devices, settings…). `except`: keys the action has just written itself (setQueryData).
 */
export function invalidateSales(qc: QueryClient, except: string[] = []) {
  return qc.invalidateQueries({
    predicate: (q) => SALES_KEYS.has(String(q.queryKey[0])) && !except.includes(String(q.queryKey[0])),
  })
}

/** A show's own mode / details changed. */
export function invalidateShow(qc: QueryClient) {
  const keys = new Set(['shows', 'show', 'show-dashboard', 'sales', 'dashboard', 'nav-counts'])
  return qc.invalidateQueries({ predicate: (q) => keys.has(String(q.queryKey[0])) })
}
