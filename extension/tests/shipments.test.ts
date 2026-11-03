import { collectAll } from '../src/shipments/collect'
import { findShipmentsTable, nextButton, onShipmentsPage, readPage, readRows } from '../src/shipments/read'
import { cleanShipments, postShipments } from '../src/shipments/upload'

/**
 * The header and pager markup as read from Seller Hub's Shipments page on 2026-11-02 (an
 * account with no shipments; classes and icons stripped). The body rows are made up: no
 * filled page has been captured yet.
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

type R = { who: string[]; date: string; items: string[]; value: string; status: string; tracking?: string }
const tr = (r: R) => `<tr>
  <td><button role="checkbox"></button></td>
  <td>${r.who.map((w) => `<div>${w}</div>`).join('')}</td>
  <td>${r.date}</td>
  <td>${r.items.map((i) => `<div>${i}</div>`).join('')}</td>
  <td>${r.value}</td><td>0.5 lb</td><td>10x8x4</td><td></td>
  <td><span>${r.status}</span></td><td>${r.tracking ?? ''}</td><td><button>Print label</button></td>
</tr>`

function page(rows: R[], from = 1, total = rows.length, extra = '') {
  document.body.innerHTML = `<main><table>${HEAD}<tbody>${rows.map(tr).join('')}${extra}</tbody></table>${pager(from, from + rows.length - 1, total, from + rows.length - 1 >= total)}</main>`
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
      { recipient: 'jess88', orderDate: 'Oct 31, 2026', items: '3 items', itemLines: [], value: '$45.20', status: 'Unfulfilled', tracking: '' },
      {
        recipient: 'tinalou13699',
        orderDate: 'Nov 1, 2026 1:05 AM',
        items: '4 In 1 Video Making Kit · Fitness Ball',
        itemLines: ['4 In 1 Video Making Kit', 'Fitness Ball'],
        value: '$33.56',
        status: 'Shipped',
        tracking: '9400 1000 0000',
      },
    ])
    expect(JSON.stringify(rows)).not.toContain('Main St') // no address leaves the page
  })

  it('an expanded row under a shipment adds its items', () => {
    page([{ who: ['jess88'], date: 'Oct 31, 2026', items: ['2'], value: '$20.00', status: 'Unfulfilled' }], 1, 1, '<tr><td colspan="11"><div>Quick Hit 2</div><div>Ultra 20</div></td></tr>')
    expect(readRows(findShipmentsTable(document)!)[0]!.itemLines).toEqual(['Quick Hit 2', 'Ultra 20'])
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

  it('collects every page by pressing Next', async () => {
    const first: R[] = [{ who: ['a1'], date: 'Oct 31, 2026', items: ['1'], value: '$1.00', status: 'Unfulfilled' }]
    const second: R[] = [{ who: ['b2'], date: 'Oct 31, 2026', items: ['2'], value: '$2.00', status: 'Unfulfilled' }]
    page(first, 1, 2)
    document.getElementById('next')!.addEventListener('click', () => setTimeout(() => page(second, 2, 2), 50))
    const pages: number[] = []
    const got = await collectAll(document, (n) => pages.push(n))
    expect(got.rows.map((r) => r.recipient)).toEqual(['a1', 'b2'])
    expect(got).toMatchObject({ total: 2, pages: 2 })
    expect(got.stoppedEarly).toBeUndefined()
    expect(pages).toEqual([1, 2])
  })
})

describe('Shipments upload', () => {
  it('cuts rows to the server limits and refuses non-lists', () => {
    expect(cleanShipments('x')).toBeNull()
    expect(cleanShipments([{ recipient: 'a'.repeat(200), itemLines: ['x'.repeat(500)] }, { recipient: '' }, null])).toEqual([
      { recipient: 'a'.repeat(80), orderDate: '', items: '', itemLines: ['x'.repeat(240)], value: '', status: '', tracking: '' },
    ])
  })

  it('posts with the device key and reports the server answer', async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ stored: 2, skipped: 0, unmatched: 1, shows: { '7': 1 } }), { status: 200 }))
    const reply = await postShipments({ serverUrl: 'https://ops.example.com', apiKey: 'k' }, [], fetchFn as unknown as typeof fetch)
    expect(reply).toEqual({ stored: 2, skipped: 0, unmatched: 1, shows: { '7': 1 } })
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('https://ops.example.com/api/ingest/shipments')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer k')
    expect(await postShipments({ serverUrl: '', apiKey: '' }, [])).toEqual({ error: 'This laptop is not connected to the web app (Settings).' })
  })
})
