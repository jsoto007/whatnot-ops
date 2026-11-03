/**
 * Whatnot's Seller Hub Shipments page (/dashboard/shipments), read after a show so the web
 * app can check its order slips against what Whatnot will ship.
 *
 * What is known (seen 2026-11-02 on an account with no shipments): a real <table> whose
 * header cells read "", Recipient, Order Date, Items, Value, Weight, Dimensions,
 * Requirements, Status, Tracking, Actions; under it a pager with Previous / Next buttons, a
 * "Show 50" page size and "Showing 1-50 of 226". What a filled row holds is NOT known yet,
 * so cells are read by their column header and kept as plain text; the server reads counts
 * and dates loosely. Nothing outside the Recipient's username is taken from that cell, so no
 * address leaves the page.
 *
 * DOM-only (no chrome.* calls), so it runs in jsdom.
 */

export interface ShipmentRow {
  recipient: string
  orderDate: string
  /** The Items cell as text ("3", "3 items", or titles). */
  items: string
  /** Item titles when the cell (or an expanded row under it) lists them. */
  itemLines: string[]
  value: string
  status: string
  tracking: string
}

export interface PageInfo {
  from: number
  to: number
  total: number
}

const COLUMNS = {
  recipient: 'recipient',
  orderDate: 'order date',
  items: 'items',
  value: 'value',
  status: 'status',
  tracking: 'tracking',
} as const
type Column = keyof typeof COLUMNS

const MAX_CELL = 1000
const MAX_LINES = 60
const USERNAME = /^@?[A-Za-z0-9._-]{2,80}$/
const COUNT_ONLY = /^\d{1,4}(\s*items?)?$/i

const norm = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim()

/** The visible text pieces of an element, one per text node, in order. */
export function leaves(el: Element): string[] {
  const out: string[] = []
  const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const p = n.parentElement
    if (p && (p.closest('button, svg, script, style, input, [aria-hidden="true"]') ?? null)) continue
    const t = norm(n.textContent)
    if (t) out.push(t)
  }
  return out
}

const headerTexts = (table: HTMLTableElement): string[] => [...(table.tHead?.rows[0]?.cells ?? [])].map((c) => leaves(c).join(' ').toLowerCase())

/** The Shipments table: the one whose headers include Recipient and Items. */
export function findShipmentsTable(doc: Document = document): HTMLTableElement | null {
  for (const t of doc.querySelectorAll('table')) {
    const h = headerTexts(t)
    if (h.includes(COLUMNS.recipient) && h.includes(COLUMNS.items)) return t
  }
  return null
}

/** The recipient's username: a leaf starting with "@", else the first leaf if it looks like one. */
function username(cell: Element): string {
  const parts = leaves(cell)
  const at = parts.find((p) => p.startsWith('@') && USERNAME.test(p))
  const first = parts[0] ?? ''
  const name = at ?? (USERNAME.test(first) ? first : '')
  return name.replace(/^@/, '')
}

/**
 * Every shipment on the current page. A row with fewer cells than the header (an expanded
 * row under a shipment) adds its text to the shipment above as item lines.
 */
export function readRows(table: HTMLTableElement): ShipmentRow[] {
  const headers = headerTexts(table)
  const at = Object.fromEntries((Object.keys(COLUMNS) as Column[]).map((k) => [k, headers.indexOf(COLUMNS[k])])) as Record<Column, number>
  const out: ShipmentRow[] = []
  for (const body of table.tBodies) {
    for (const tr of body.rows) {
      const cells = tr.cells
      if (cells.length !== headers.length) {
        const last = out[out.length - 1]
        if (last) last.itemLines.push(...leaves(tr).slice(0, MAX_LINES - last.itemLines.length))
        continue
      }
      const cell = (c: Column) => (at[c] >= 0 ? cells[at[c]] : undefined)
      const text = (c: Column) => {
        const el = cell(c)
        return el ? leaves(el).join(' · ').slice(0, MAX_CELL) : ''
      }
      const rcp = cell('recipient')
      const items = cell('items')
      const itemParts = items ? leaves(items) : []
      out.push({
        recipient: rcp ? username(rcp) : '',
        orderDate: text('orderDate'),
        items: text('items'),
        itemLines: itemParts.length > 1 || (itemParts.length === 1 && !COUNT_ONLY.test(itemParts[0]!)) ? itemParts.slice(0, MAX_LINES) : [],
        value: text('value'),
        status: text('status'),
        tracking: text('tracking'),
      })
    }
  }
  return out.filter((r) => r.recipient)
}

const SHOWING = /^Showing (\d+)\s*[-–]\s*(\d+) of (\d+)$/i

function showingEl(doc: Document): Element | null {
  for (const el of doc.querySelectorAll('strong, span, div, p')) {
    if (el.children.length === 0 && SHOWING.test(norm(el.textContent))) return el
  }
  return null
}

/** "Showing 51-100 of 226" → { from: 51, to: 100, total: 226 }. */
export function readPage(doc: Document = document): PageInfo | null {
  const el = showingEl(doc)
  const m = el ? SHOWING.exec(norm(el.textContent)) : null
  return m ? { from: Number(m[1]), to: Number(m[2]), total: Number(m[3]) } : null
}

/** The pager's Next button (the second of its two arrow buttons), or null when disabled or not found. */
export function nextButton(doc: Document = document): HTMLButtonElement | null {
  let box: Element | null = showingEl(doc)
  for (let i = 0; box && i < 5; i++, box = box.parentElement) {
    const arrows = [...box.querySelectorAll('button')].filter((b) => !norm(b.textContent) && !b.closest('[role="combobox"], [aria-haspopup]'))
    if (arrows.length >= 2) {
      const next = arrows[1]!
      return next.disabled || next.getAttribute('aria-disabled') === 'true' ? null : next
    }
  }
  return null
}

export const onShipmentsPage = (pathname: string) => pathname.startsWith('/dashboard/shipments')
