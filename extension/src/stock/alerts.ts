/** Low-stock alerts for the panel: 3, 2 and 1 left, then sold out. Pure, so it runs in Vitest. */

export interface StockItem {
  skuId: number
  code: string
  name: string
  /** on hand minus what sales hold; negative once oversold */
  available: number
}

export interface StockAlert {
  item: StockItem
  text: string
  urgent: boolean
}

/** Last seen `available` per SKU. A SKU missing from the server's list has more than 3. */
export type StockSeen = Map<number, number>

export function leftText(available: number): string {
  if (available > 0) return `${available} left`
  return available === 0 ? 'sold out' : `oversold by ${-available}`
}

/**
 * Compare a fresh poll with the last one. An item alerts when it drops to 3, 2 or 1
 * left, or to sold out; it does not alert again while it stays there, nor for each
 * further oversell (overselling is allowed). A restock or a cancel moves it back up,
 * so the next sale can alert again.
 *
 * The first poll of a recording (`seen` null) only sets the baseline: anything that was
 * already low before Start shows in the list, but does not alert.
 */
export function stockAlerts(seen: StockSeen | null, items: StockItem[], alertAt = 3): { alerts: StockAlert[]; seen: StockSeen } {
  const next: StockSeen = new Map(items.map((i) => [i.skuId, i.available]))
  if (!seen) return { alerts: [], seen: next }
  const alerts: StockAlert[] = []
  for (const item of items) {
    const before = seen.get(item.skuId) ?? alertAt + 1
    const now = item.available
    if (now >= before) continue
    if ((now > 0 && now <= alertAt) || (now <= 0 && before > 0)) {
      alerts.push({ item, text: `${leftText(now)}: ${item.name} (${item.code})`, urgent: now <= 1 })
    }
  }
  return { alerts, seen: next }
}
