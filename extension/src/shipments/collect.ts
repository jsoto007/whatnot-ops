import { findShipmentsTable, isExpanded, nextButton, readPage, readRows, readShowFilter, shipmentRows, toggleButton, type PageInfo, type ShipmentRow, type ShowFilter } from './read'

/** A show is a few hundred shipments; 50 a page. Past this something is wrong: stop. */
export const MAX_PAGES = 40
const WAIT_MS = 15_000
const POLL_MS = 250
/** One row's lots load after Expand; past this the row is read without them. */
const EXPAND_MS = 8_000

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function until<T>(get: () => T | null | undefined, ms: number): Promise<T | null> {
  const end = Date.now() + ms
  for (;;) {
    const v = get()
    if (v) return v
    if (Date.now() >= end) return null
    await sleep(POLL_MS)
  }
}

export interface Collected {
  rows: ShipmentRow[]
  /** Whatnot's show filter as it was when reading started. */
  show: ShowFilter | null
  /** Shipments whose lots could not be opened (sent with their counts only). */
  noLots: number
  /** "of N" from the pager, when it was read. */
  total: number | null
  pages: number
  /** Why reading stopped before the last page, if it did. */
  stoppedEarly?: string
}

/**
 * Open every shipment on the page (Expand) so its lots are on the page, one at a time.
 * Returns the rows this opened, to close again, and how many never showed their lots.
 */
export async function expandAll(table: HTMLTableElement): Promise<{ opened: HTMLTableRowElement[]; failed: number }> {
  const opened: HTMLTableRowElement[] = []
  let failed = 0
  for (const tr of shipmentRows(table)) {
    if (isExpanded(tr)) continue
    const b = toggleButton(tr, 'Expand')
    if (!b) continue
    b.click()
    if (await until(() => isExpanded(tr), EXPAND_MS)) opened.push(tr)
    else failed += 1
  }
  return { opened, failed }
}

function collapse(rows: HTMLTableRowElement[]): void {
  for (const tr of rows) if (tr.isConnected) toggleButton(tr, 'Collapse')?.click()
}

/**
 * Read every page of the Shipments table as Whatnot shows it now (its show and status
 * filters as the person set them), opening each shipment for its lots and pressing the
 * pager's Next button between pages.
 */
export async function collectAll(doc: Document = document, onPage?: (n: number, info: PageInfo | null) => void): Promise<Collected> {
  const rows: ShipmentRow[] = []
  let pages = 0
  let noLots = 0
  let info: PageInfo | null = null
  // Wait for real rows, not the grey placeholders shown while the page loads (its pager
  // reads "Showing 0-0 of 0" meanwhile, so that is no sign of an empty list).
  const table = await until(() => {
    const t = findShipmentsTable(doc)
    return t && readRows(t).length ? t : null
  }, WAIT_MS)
  const show = readShowFilter(doc)
  if (!table) {
    const empty = findShipmentsTable(doc) ? 'No shipments showed up. Check the show and status filters.' : 'The Shipments table did not show up.'
    return { rows, show, noLots, total: null, pages, stoppedEarly: empty }
  }
  const done = (stoppedEarly?: string): Collected => ({ rows, show, noLots, total: info?.total ?? null, pages, ...(stoppedEarly ? { stoppedEarly } : {}) })
  for (;;) {
    const t = findShipmentsTable(doc)
    if (!t) return done('The Shipments table went away while reading.')
    const { opened, failed } = await expandAll(t)
    const got = readRows(t)
    noLots += failed
    collapse(opened)
    rows.push(...got)
    pages += 1
    info = readPage(doc)
    onPage?.(pages, info)
    if (!info || info.to >= info.total) return done()
    if (pages >= MAX_PAGES) return done(`Stopped after ${MAX_PAGES} pages.`)
    const next = nextButton(doc)
    if (!next) return done('Could not find the Next page button.')
    const from = info.from
    next.click()
    const moved = await until(() => {
      const p = readPage(doc)
      return p && p.from !== from ? p : null
    }, WAIT_MS)
    if (!moved) return done('The next page did not load.')
    await sleep(POLL_MS) // let the rows render with the new pager text
  }
}
