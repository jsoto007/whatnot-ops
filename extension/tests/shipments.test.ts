import { collectAll } from '../src/shipments/collect'
import { findShipmentsTable, nextButton, onShipmentsPage, readLots, readPage, readRows, readShowFilter } from '../src/shipments/read'
import { cleanShipments, postShipments } from '../src/shipments/upload'

/**
 * Header, pager, row, expanded-lot and show-filter markup as read from Seller Hub's Shipments
 * page (2026-11-02 empty, 2026-11-05 filled on the shop's account; classes, icons and images
 * stripped). Buyers, order numbers and prices are made up.
 */
const HEAD = `<thead><tr>
  <th><div><div><div><button role="checkbox" aria-checked="false"></button><input type="checkbox" tabindex="-1" aria-hidden="true"></div></div></div></th>
  <th aria-sort="none"><div><section><button type="button"><div><svg></svg></div></button>Recipient</section><div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Order Date<div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Items<div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Value<div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Weight<div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Dimensions<div><svg></svg></div></div></th>
  <th><div>Requirements</div></th>
  <th aria-sort="none"><div>Status<div><svg></svg></div></div></th>
  <th aria-sort="none"><div>Tracking<div><svg></svg></div></div></th>
  <th><div>Actions</div></th>
</tr></thead>`

const pager = (from: number, to: number, total: number, nextDisabled = false) => `
<div id="pager"><div><button ${from <= 1 ? 'disabled' : ''}><div><svg></svg></div></button><button id="next" ${nextDisabled ? 'disabled' : ''}><div><svg></svg></div></button></div>
<div><div><div aria-haspopup="dialog" role="button"><input type="hidden" value="50"><input role="combobox" readonly value="Show 50"><button type="button" tabindex="-1"><div><svg></svg></div></button></div></div></div>
<strong>Showing ${from}-${to} of ${total}</strong></div>`

type Lot = [title: string, order: string, qty: string, price: string, shipping: string, type: string]
type R = { who: string[]; date: string; items: string[]; value: string; status: string; tracking?: string; lots?: Lot[] }
const toggle = (word: 'Expand' | 'Collapse') => `<button type="button"><div><svg></svg></div><span>${word}</span></button>`
const tr = (r: R) => `<tr>
  <td><button role="checkbox"></button></td>
  <td>${r.who.map((w) => `<div>${w}</div>`).join('')}${r.lots ? toggle('Expand') : ''}</td>
  <td>${r.date}</td>
  <td>${r.items.map((i) => `<div>${i}</div>`).join('')}</td>
  <td>${r.value}</td><td>0.5 lb</td><td>10x8x4</td><td></td>
  <td><span>${r.status}</span></td><td>${r.tracking ?? ''}</td><td><button>Print label</button></td>
</tr>`

/** The single-cell row Expand puts under a shipment: "Items (Bundled)" and a table of lots. */
const detail = (lots: Lot[]) => `<tr><td colspan="11"><div><section><strong>Items (Bundled)</strong></section><table>
  <thead><tr><th><div>Listing</div></th><th><div>Qty</div></th><th><div>Price</div></th><th><div>Weight</div></th><th><div>Shipping</div></th><th><div>Hazmat</div></th><th><div>Sale Type</div></th><th><div></div></th></tr></thead>
  <tbody>${lots
    .map(
      ([title, order, qty, price, ship, type]) => `<tr>
    <td><div><div><img alt=""></div><div><strong>${title}</strong><a href="#o"><strong>Order #${order}</strong></a></div></div></td>
    <td><section>${qty}</section></td><td>${price}</td><td>10 oz</td><td>${ship}</td>
    <td><span>Hazmat<div><svg role="presentation"><path></path></svg></div></span></td><td>${type}</td>
    <td><section><span role="button"><button><span>Video Receipt</span></button></span></section></td></tr>`,
    )
    .join('')}</tbody></table></div></td></tr>`

const SHOW_FILTER = (label: string) => `<div><button type="button">${label}<div><svg></svg></div></button><button type="button">Unfulfilled<div><svg></svg></div></button><button>Reset</button></div>`

/** Like the real page: Expand shows the lots a moment later; Collapse takes them away. */
function wireToggles(delay = 20) {
  document.querySelector('tbody')!.addEventListener('click', (e) => {
    const b = (e.target as Element).closest('button')
    const row = b?.closest('tr') as HTMLTableRowElement | null
    if (!b || !row || row.cells.length !== 11) return
    const r = rowsByName.get(row.cells[1]!.querySelector('div')!.textContent!)
    if (!r?.lots) return
    if (b.textContent === 'Expand') {
      b.querySelector('span')!.textContent = 'Collapse'
      setTimeout(() => row.insertAdjacentHTML('afterend', detail(r.lots!)), delay)
    } else {
      b.querySelector('span')!.textContent = 'Expand'
      row.nextElementSibling?.remove()
    }
  })
}
const rowsByName = new Map<string, R>()

function page(rows: R[], from = 1, total = rows.length, extra = '', filter = '') {
  for (const r of rows) rowsByName.set(r.who[0]!, r)
  document.body.innerHTML = `<main>${filter}<table>${HEAD}<tbody>${rows.map(tr).join('')}${extra}</tbody></table>${pager(from, from + rows.length - 1, total, from + rows.length - 1 >= total)}</main>`
  wireToggles()
}

describe('Shipments page reader', () => {
  it('reads each row by its column header and keeps only the username from Recipient', () => {
    page([
      { who: ['jess88', 'Jessica A.', '12 Main St', 'Austin, TX 78701'], date: 'Oct 31, 2026', items: ['3 items'], value: '$45.20', status: 'Unfulfilled' },
      { who: ['Tina L.', '@tinalou13699'], date: 'Nov 1, 2026 1:05 AM', items: ['4 In 1 Video Making Kit', 'Fitness Ball'], value: '$33.56', status: 'Shipped', tracking: '9400 1000 0000' },
      { who: ['Jane Doe', '45 Oak Ave'], date: 'Oct 31, 2026', items: ['1'], value: '$5.00', status: 'Unfulfilled' }, // no username: dropped
    ])
    const t = findShipmentsTable(document)!
    expect(t).not.toBeNull()
    const rows = readRows(t)
    expect(rows).toEqual([
      { recipient: 'jess88', orderDate: 'Oct 31, 2026', items: '3 items', itemLines: [], value: '$45.20', status: 'Unfulfilled', tracking: '', lots: [] },
      {
        recipient: 'tinalou13699',
        orderDate: 'Nov 1, 2026 1:05 AM',
        items: '4 In 1 Video Making Kit · Fitness Ball',
        itemLines: ['4 In 1 Video Making Kit', 'Fitness Ball'],
        value: '$33.56',
        status: 'Shipped',
        tracking: '9400 1000 0000',
        lots: [],
      },
    ])
    expect(JSON.stringify(rows)).not.toContain('Main St') // no address leaves the page
  })

  it('an expanded row under a shipment adds its items', () => {
    page([{ who: ['jess88'], date: 'Oct 31, 2026', items: ['2'], value: '$20.00', status: 'Unfulfilled' }], 1, 1, '<tr><td colspan="11"><div>Quick Hit 2</div><div>Ultra 20</div></td></tr>')
    expect(readRows(findShipmentsTable(document)!)[0]!.itemLines).toEqual(['Quick Hit 2', 'Ultra 20'])
  })

  it('reads the lots in an expanded row: title, Whatnot order number, qty, price, shipping, sale type', () => {
    page([{ who: ['jess88', 'New'], date: 'Oct 22', items: ['2'], value: '$32.00', status: 'Ready to Ship', lots: [] }], 1, 1,
      detail([
        ['CHOOSE ANY PRODUCT YOU WANT #73', '1462657228', '1', '$17.00', '$5.25', 'Auction'],
        ['CHOOSE ANY PRODUCT YOU WANT #79', '1462660717', '1', '$15.00', '$0.00', 'Auction'],
      ]))
    const [row] = readRows(findShipmentsTable(document)!)
    expect(row!.recipient).toBe('jess88') // not "New", not "Expand"
    expect(row!.itemLines).toEqual([])
    expect(row!.lots).toEqual([
      { title: 'CHOOSE ANY PRODUCT YOU WANT #73', orderId: '1462657228', quantity: '1', price: '$17.00', shipping: '$5.25', saleType: 'Auction' },
      { title: 'CHOOSE ANY PRODUCT YOU WANT #79', orderId: '1462660717', quantity: '1', price: '$15.00', shipping: '$0.00', saleType: 'Auction' },
    ])
    expect(JSON.stringify(row)).not.toContain('Video Receipt')
    expect(readLots(document.createElement('tr'))).toBeNull()
  })

  it("reads Whatnot's show filter, and knows when no single show is picked", () => {
    page([], 1, 0, '', SHOW_FILTER('$1 START ON EVERYTHING + $10,000 On Amazon Gift Cards Oct 22•1:44 AM EDT'))
    expect(readShowFilter(document)).toEqual({ label: '$1 START ON EVERYTHING + $10,000 On Amazon Gift Cards Oct 22•1:44 AM EDT' })
    page([], 1, 0, '', SHOW_FILTER('All Shows & Marketplace'))
    expect(readShowFilter(document)).toEqual({ all: true })
    page([], 1, 0, '', SHOW_FILTER('Shows • Marketplace')) // while the page loads
    expect(readShowFilter(document)).toEqual({ all: true })
    page([], 1, 0)
    expect(readShowFilter(document)).toBeNull()
  })

  it('reads the pager and finds Next, not the page-size toggle', () => {
    page([{ who: ['a1'], date: 'Oct 31, 2026', items: ['1'], value: '$1.00', status: 'x' }], 1, 120)
    expect(readPage(document)).toEqual({ from: 1, to: 1, total: 120 })
    expect(nextButton(document)?.id).toBe('next')
    page([{ who: ['a1'], date: 'Oct 31, 2026', items: ['1'], value: '$1.00', status: 'x' }], 1, 1)
    expect(nextButton(document)).toBeNull() // last page: Next is disabled
  })

  it('ignores other tables and knows its page', () => {
    document.body.innerHTML = '<table><thead><tr><th>Name</th><th>Items</th></tr></thead></table>'
    expect(findShipmentsTable(document)).toBeNull()
    expect(onShipmentsPage('/dashboard/shipments')).toBe(true)
    expect(onShipmentsPage('/live/abc')).toBe(false)
  })

  it('collects every page by pressing Next, opening each shipment for its lots and closing it again', async () => {
    const filter = SHOW_FILTER('Big show Oct 22•1:44 AM EDT')
    const first: R[] = [
      { who: ['a1'], date: 'Oct 22', items: ['1'], value: '$1.00', status: 'Unfulfilled', lots: [['Quick Hit 2', '11', '1', '$1.00', '$0.00', 'Auction']] },
      { who: ['c3'], date: 'Oct 22', items: ['1'], value: '$3.00', status: 'Unfulfilled', lots: [['Ultra 9', '33', '1', '$3.00', '$0.00', 'Auction']] },
    ]
    const second: R[] = [{ who: ['b2'], date: 'Oct 22', items: ['2'], value: '$2.00', status: 'Unfulfilled', lots: [['Listing #4', '22', '2', '$2.00', '$0.00', 'Auction']] }]
    page(first, 1, 3, '', filter)
    document.getElementById('next')!.addEventListener('click', () => setTimeout(() => page(second, 3, 3, '', filter), 50))
    const pages: number[] = []
    const got = await collectAll(document, (n) => pages.push(n))
    expect(got.rows.map((r) => [r.recipient, r.lots.map((l) => l.orderId)])).toEqual([
      ['a1', ['11']],
      ['c3', ['33']],
      ['b2', ['22']],
    ])
    expect(got).toMatchObject({ total: 3, pages: 2, noLots: 0, show: { label: 'Big show Oct 22•1:44 AM EDT' } })
    expect(got.stoppedEarly).toBeUndefined()
    expect(pages).toEqual([1, 2])
    expect(document.querySelectorAll('tbody tr').length).toBe(1) // closed again
  })

  it('waits out the loading placeholders before reading', async () => {
    document.body.innerHTML = `<main><table>${HEAD}<tbody><tr>${'<td><div></div></td>'.repeat(11)}</tr></tbody></table>${pager(0, 0, 0)}</main>`
    setTimeout(() => page([{ who: ['late1'], date: 'Oct 22', items: ['1'], value: '$1.00', status: 'x', lots: [['Quick Hit 1', '9', '1', '$1.00', '$0.00', 'Auction']] }], 1, 1), 300)
    const got = await collectAll(document)
    expect(got.rows.map((r) => r.recipient)).toEqual(['late1'])
    expect(got.rows[0]!.lots).toHaveLength(1)
  })
})

describe('Shipments upload', () => {
  it('cuts rows to the server limits and refuses non-lists', () => {
    expect(cleanShipments('x')).toBeNull()
    expect(cleanShipments([{ recipient: 'a'.repeat(200), itemLines: ['x'.repeat(500)] }, { recipient: '' }, null])).toEqual([
      { recipient: 'a'.repeat(80), orderDate: '', items: '', itemLines: ['x'.repeat(240)], value: '', status: '', tracking: '', lots: [] },
    ])
    const [r] = cleanShipments([{ recipient: 'a1', lots: [{ title: 'Ultra 9', orderId: '12x', price: '$3.00' }, { orderId: '55' }, 'junk', {}] }])!
    expect(r!.lots).toEqual([
      { title: 'Ultra 9', orderId: '', quantity: '', price: '$3.00', shipping: '', saleType: '' }, // not a number: dropped
      { title: '', orderId: '55', quantity: '', price: '', shipping: '', saleType: '' },
    ])
  })

  it('posts with the device key and reports the server answer', async () => {
    const answer = { stored: 2, lots: 3, show: { id: 7, date: '2026-10-21' }, skipped: 0, unmatched: 0, shows: { '7': 2 } }
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 }))
    const reply = await postShipments({ serverUrl: 'https://ops.example.com', apiKey: 'k' }, [], 'Big show Oct 22•1:44 AM EDT', fetchFn as unknown as typeof fetch)
    expect(reply).toEqual(answer)
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('https://ops.example.com/api/ingest/shipments')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer k')
    expect(JSON.parse(init.body as string)).toEqual({ show: 'Big show Oct 22•1:44 AM EDT', rows: [] })
    expect(await postShipments({ serverUrl: '', apiKey: '' }, [], '')).toEqual({ error: 'This laptop is not connected to the web app (Settings).' })
  })
})
