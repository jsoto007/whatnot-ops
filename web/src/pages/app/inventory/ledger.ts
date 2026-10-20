import { fmtDate } from '../../../lib/format'
import type { Activity, StockLevel } from '../../../lib/api'

export type Tone = 'in' | 'out' | 'held' | 'neutral'

/** One ledger row in words: what happened, and what it did to the shelf or the held count. */
export function describe(m: Activity): { text: string; effect: string; tone: Tone } {
  const q = Math.abs(m.quantity)
  const who = m.sale ? `${m.sale.buyer} · ${fmtDate(m.sale.showDate)} show` : ''
  const note = m.reason ? ` · ${m.reason}` : ''
  switch (m.kind) {
    case 'restock':
      return { text: (m.sourceType === 'receive' ? `Received ${q}` : `Restocked ${q}`) + note, effect: `+${q} on shelf`, tone: 'in' }
    case 'adjust':
      if (m.sourceType === 'count') return { text: `Counted: shelf set to ${m.onHandAfter}${note && note !== ' · stock count' ? note : ''}`, effect: `${m.quantity > 0 ? '+' : '−'}${q} on shelf`, tone: m.quantity > 0 ? 'in' : 'out' }
      if (m.sourceType === 'import') return { text: `Sheet import: shelf set to ${m.onHandAfter}`, effect: `${m.quantity > 0 ? '+' : '−'}${q} on shelf`, tone: m.quantity > 0 ? 'in' : 'out' }
      return { text: `${m.quantity > 0 ? 'Added' : 'Removed'} ${q}${note}`, effect: `${m.quantity > 0 ? '+' : '−'}${q} on shelf`, tone: m.quantity > 0 ? 'in' : 'out' }
    case 'reserve':
      return { text: `Sold ${q}${who ? ` to ${who}` : ''}`, effect: `${q} held`, tone: 'held' }
    case 'release':
      return { text: `Sale cancelled${who ? ` · ${who}` : ''}`, effect: `${q} back to sell`, tone: 'neutral' }
    case 'commit':
      return { text: `Paid${who ? ` by ${who}` : ''}`, effect: `−${q} on shelf`, tone: 'out' }
  }
}

/** Where one item stands, in the order the team should act on it. */
export function stateOf(s: StockLevel): { label: string; tone: 'red' | 'amber' | 'grey' | 'green' } {
  if (s.short > 0) return { label: `Oversold · buy ${s.short}`, tone: 'red' }
  if (s.available <= 0) return { label: 'Out', tone: 'red' }
  if (s.low) return { label: 'Low', tone: 'amber' }
  return { label: 'In stock', tone: 'green' }
}
