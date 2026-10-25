import { leftText, stockAlerts, type StockItem, type StockSeen } from '../src/stock/alerts'
import { fetchStock } from '../src/stock/fetch'
import type { Settings } from '../src/upload/settings'

const item = (available: number, skuId = 1): StockItem => ({ skuId, code: 'RED', name: 'Red bundle', available })

/** Feed a sequence of polls through stockAlerts and collect what each one said. */
function run(polls: StockItem[][]): string[][] {
  let seen: StockSeen | null = null
  return polls.map((items) => {
    const r = stockAlerts(seen, items)
    seen = r.seen
    return r.alerts.map((a) => a.text)
  })
}

describe('stockAlerts', () => {
  it('counts an item down: 3, 2, 1, sold out, once each', () => {
    expect(run([[], [item(3)], [item(3)], [item(2)], [item(1)], [item(1)], [item(0)]])).toEqual([
      [],
      ['3 left: Red bundle (RED)'],
      [],
      ['2 left: Red bundle (RED)'],
      ['1 left: Red bundle (RED)'],
      [],
      ['sold out: Red bundle (RED)'],
    ])
  })

  it('a jump says where it landed, not every level it passed', () => {
    expect(run([[], [item(2)], [item(-1)]])).toEqual([[], ['2 left: Red bundle (RED)'], ['oversold by 1: Red bundle (RED)']])
  })

  it('does not alert for each further oversell', () => {
    expect(run([[item(0)], [item(-1)], [item(-2)]])).toEqual([[], [], []])
  })

  it('the first poll is only the baseline, even for items already low', () => {
    expect(run([[item(1), item(2, 2)]])).toEqual([[]])
  })

  it('a restock or cancel moves it back up, so the next drop alerts again', () => {
    // 1 left → cancel gives one back (2) → sold again (1): alert again. Restocked past 3 → gone from the list.
    expect(run([[], [item(1)], [item(2)], [item(1)], [], [item(3)]])).toEqual([
      [],
      ['1 left: Red bundle (RED)'],
      [],
      ['1 left: Red bundle (RED)'],
      [],
      ['3 left: Red bundle (RED)'],
    ])
  })

  it('1 left and sold out are urgent', () => {
    const urgent = (n: number) => stockAlerts(new Map(), [item(n)]).alerts[0]?.urgent
    expect([urgent(3), urgent(2), urgent(1), urgent(0)]).toEqual([false, false, true, true])
  })

  it('words the level', () => {
    expect([leftText(2), leftText(0), leftText(-3)]).toEqual(['2 left', 'sold out', 'oversold by 3'])
  })
})

describe('fetchStock', () => {
  const settings: Settings = { serverUrl: 'https://shop.example', apiKey: 'k' } as Settings

  it('asks the server with the device key', async () => {
    const calls: [string, RequestInit | undefined][] = []
    const fetchFn = (async (url: string, init?: RequestInit) => {
      calls.push([url, init])
      return new Response(JSON.stringify({ alertAt: 3, items: [item(2)] }), { status: 200 })
    }) as typeof fetch
    expect(await fetchStock(settings, undefined, fetchFn)).toEqual({ alertAt: 3, items: [item(2)] })
    const [url, init] = calls[0]!
    expect(url).toBe('https://shop.example/api/ingest/stock')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer k')
  })

  it('asks only about this show when it has one', async () => {
    const urls: string[] = []
    const fetchFn = (async (url: string) => {
      urls.push(url)
      return new Response(JSON.stringify({ alertAt: 3, items: [] }), { status: 200 })
    }) as typeof fetch
    await fetchStock(settings, '2026-10-24T23:00:00.000Z', fetchFn)
    expect(urls).toEqual(['https://shop.example/api/ingest/stock?show=2026-10-24T23%3A00%3A00.000Z'])
  })

  it('is null when not connected or the server says no', async () => {
    const no = (async () => new Response('', { status: 401 })) as typeof fetch
    expect(await fetchStock({ ...settings, apiKey: '' }, undefined, no)).toBeNull()
    expect(await fetchStock(settings, undefined, no)).toBeNull()
    const offline = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    expect(await fetchStock(settings, undefined, offline)).toBeNull()
  })

  it('drops anything that is not a stock row', async () => {
    const odd = (async () =>
      new Response(JSON.stringify({ alertAt: 3, items: [item(1), { skuId: 'x' }, null] }), { status: 200 })) as typeof fetch
    expect(await fetchStock(settings, undefined, odd)).toEqual({ alertAt: 3, items: [item(1)] })
  })
})
