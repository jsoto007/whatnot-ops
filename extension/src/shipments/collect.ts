import { findShipmentsTable, nextButton, readPage, readRows, type PageInfo, type ShipmentRow } from './read'

/** A show is a few hundred shipments; 50 a page. Past this something is wrong: stop. */
export const MAX_PAGES = 40
const WAIT_MS = 15_000
const POLL_MS = 250

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
  /** "of N" from the pager, when it was read. */
  total: number | null
  pages: number
  /** Why reading stopped before the last page, if it did. */
  stoppedEarly?: string
}

/**
 * Read every page of the Shipments table as Whatnot shows it now (its show and status
 * filters as the person set them), pressing the pager's Next button between pages.
 */
export async function collectAll(doc: Document = document, onPage?: (n: number, info: PageInfo | null) => void): Promise<Collected> {
  const rows: ShipmentRow[] = []
  let pages = 0
  let info: PageInfo | null = null
  const table = await until(() => findShipmentsTable(doc), WAIT_MS)
  if (!table) return { rows, total: null, pages, stoppedEarly: 'The Shipments table did not show up.' }
  for (;;) {
    const t = findShipmentsTable(doc)
    if (!t) return { rows, total: info?.total ?? null, pages, stoppedEarly: 'The Shipments table went away while reading.' }
    rows.push(...readRows(t))
    pages += 1
    info = readPage(doc)
    onPage?.(pages, info)
    if (!info || info.to >= info.total) return { rows, total: info?.total ?? null, pages }
    if (pages >= MAX_PAGES) return { rows, total: info.total, pages, stoppedEarly: `Stopped after ${MAX_PAGES} pages.` }
    const next = nextButton(doc)
    if (!next) return { rows, total: info.total, pages, stoppedEarly: 'Could not find the Next page button.' }
    const from = info.from
    next.click()
    const moved = await until(() => {
      const p = readPage(doc)
      return p && p.from !== from ? p : null
    }, WAIT_MS)
    if (!moved) return { rows, total: info.total, pages, stoppedEarly: 'The next page did not load.' }
    await sleep(POLL_MS) // let the rows render with the new pager text
  }
}
