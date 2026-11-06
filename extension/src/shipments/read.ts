/**
 * Whatnot's Seller Hub Shipments page (/dashboard/shipments), read after a show so the web
 * app can check its order slips against what Whatnot will ship.
 *
 * Seen filled on the shop's account (2026-11-05):
 * - A real <table> whose header cells read "", Recipient, Order Date, Items, Value, Weight,
 *   Dimensions, Requirements, Status, Tracking, Actions; a pager with Previous / Next
 *   buttons, "Show 50" and "Showing 1-50 of 226". Rows are grey placeholders while loading.
 * - One row per shipment (a buyer may have several). Recipient holds the username, maybe a
 *   "New" badge and an Expand / Collapse button. Order Date is "Oct 22": no year, no time.
 * - Expand adds one single-cell row holding a nested table, one line per lot: Listing
 *   ("CHOOSE ANY PRODUCT YOU WANT #73" and "Order #1462657228" in two <strong>s), Qty,
 *   Price, Weight, Shipping, Hazmat, Sale Type, and a Video Receipt button. The listing never
 *   names the product: the winner picks it in chat.
 * - The show filter is a button reading "<show title> Oct 22•1:44 AM EDT" once a show is
 *   picked, "All Shows & Marketplace" (or "Shows • Marketplace" while loading) otherwise.
 * Cells are read by their column header. Nothing outside the Recipient's username is taken
 * from that cell, so no address leaves the page.
 *
 * DOM-only (no chrome.* calls), so it runs in jsdom.
 */

/** One lot (one Whatnot order) inside a shipment, from its expanded row. */
export interface ShipmentLot {
  /** The listing title, e.g. "CHOOSE ANY PRODUCT YOU WANT #73". */
  title: string
  /** Whatnot's order number for this lot ("1462657228"); '' when not shown. */
  orderId: string
  quantity: string
  price: string
  shipping: string
  /** "Auction", or another sale type (giveaways, Buy It Now) as Whatnot names it. */
  saleType: string
}

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
  /** Its lots, when the row was expanded. */
  lots: ShipmentLot[]
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
const MAX_LOTS = 200
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
        if (!last) continue
        const lots = readLots(tr)
        if (lots) last.lots.push(...lots.slice(0, MAX_LOTS - last.lots.length))
        else last.itemLines.push(...leaves(tr).slice(0, MAX_LINES - last.itemLines.length))
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
        lots: [],
      })
    }
  }
  return out.filter((r) => r.recipient)
}

const LOT_COLUMNS = { quantity: 'qty', price: 'price', shipping: 'shipping', saleType: 'sale type' } as const
const ORDER_NO = /^Order\s*#\s*(\d{1,20})$/i

/** The lots in an expanded row's nested table (Listing, Qty, Price, …); null when it has none. */
export function readLots(detail: Element): ShipmentLot[] | null {
  const t = detail.querySelector('table')
  if (!t?.tHead?.rows[0]) return null
  const heads = [...t.tHead.rows[0].cells].map((c) => leaves(c).join(' ').toLowerCase())
  const listing = heads.indexOf('listing')
  if (listing < 0) return null
  const at = (k: keyof typeof LOT_COLUMNS) => heads.indexOf(LOT_COLUMNS[k])
  const out: ShipmentLot[] = []
  for (const body of t.tBodies) {
    for (const row of body.rows) {
      const cell = (i: number) => (i >= 0 && row.cells[i] ? leaves(row.cells[i]!).join(' ').slice(0, 80) : '')
      const parts = row.cells[listing] ? leaves(row.cells[listing]!) : []
      const order = parts.map((p) => ORDER_NO.exec(p)).find((m) => m)
      const title = parts.find((p) => !ORDER_NO.test(p)) ?? ''
      if (!title && !order) continue
      out.push({
        title: title.slice(0, 240),
        orderId: order ? order[1]! : '',
        quantity: cell(at('quantity')),
        price: cell(at('price')),
        shipping: cell(at('shipping')),
        saleType: cell(at('saleType')),
      })
    }
  }
  return out
}

/** The Expand / Collapse toggle in a shipment row. */
export function toggleButton(tr: HTMLTableRowElement, word: 'Expand' | 'Collapse'): HTMLButtonElement | null {
  for (const b of tr.querySelectorAll('button')) if (norm(b.textContent) === word) return b
  return null
}

/** The shipment rows of a table (not the expanded detail rows, not loading placeholders). */
export function shipmentRows(table: HTMLTableElement): HTMLTableRowElement[] {
  const n = headerTexts(table).length
  return [...table.tBodies].flatMap((b) => [...b.rows]).filter((tr) => tr.cells.length === n && !!toggleButton(tr, 'Expand') !== !!toggleButton(tr, 'Collapse'))
}

/** Is this shipment row's expanded detail (with its lots table) on the page? */
export const isExpanded = (tr: HTMLTableRowElement): boolean => {
  const next = tr.nextElementSibling
  return next instanceof HTMLTableRowElement && next.cells.length === 1 && !!next.querySelector('table tbody tr')
}

export type ShowFilter = { label: string } | { all: true }

const MONTHS = '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\\.?'
const SHOW_WHEN = new RegExp(`\\b${MONTHS}\\s+\\d{1,2}(?:,?\\s+\\d{4})?\\s*[•·,-]?\\s*\\d{1,2}:\\d{2}\\s*[AaPp][Mm]`)

/**
 * Whatnot's show filter: the picked show's label ("…Gift Cards Oct 22•1:44 AM EDT"), or
 * { all: true } while no single show is picked. null when the filter is not on the page.
 */
export function readShowFilter(doc: Document = document, table: Element | null = findShipmentsTable(doc)): ShowFilter | null {
  let all = false
  for (const b of doc.querySelectorAll('button, [role="button"], [role="combobox"]')) {
    if (table?.contains(b)) continue
    const t = norm(b.textContent)
    if (!t || t.length > 300) continue
    if (SHOW_WHEN.test(t)) return { label: t }
    if (/^(all\s+)?shows\b/i.test(t)) all = true
  }
  return all ? { all: true } : null
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
