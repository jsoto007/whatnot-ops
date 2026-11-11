import { type BuyerSeen, buyerLine, fetchBuyers } from '../src/buyers/known'
import type { Settings } from '../src/upload/settings'

const seen = (over: Partial<BuyerSeen> = {}): BuyerSeen => ({ name: 'lani808', flag: null, firstTime: false, shows: 5, items: 12, ...over })

describe('buyerLine', () => {
  it('says whether the winner is new or back, in words', () => {
    expect(buyerLine(seen({ firstTime: true, shows: 0, items: 0 }))).toBe('New buyer')
    expect(buyerLine(seen())).toBe('Back again · 12 items over 5 shows')
    expect(buyerLine(seen({ shows: 1, items: 1 }))).toBe('Back again · 1 item over 1 show')
  })

  it("puts the team's flag first", () => {
    expect(buyerLine(seen({ flag: 'vip' }))).toBe('VIP · back again · 12 items over 5 shows')
    expect(buyerLine(seen({ flag: 'watch', firstTime: true, shows: 0, items: 0 }))).toBe('Watch list · new buyer')
  })
})

describe('fetchBuyers', () => {
  const settings: Settings = { serverUrl: 'https://shop.example', apiKey: 'k' } as Settings
  const SHOW = '2026-10-24T23:00:00.000Z'

  it('asks the web app with the device key; the names go in the body, never the address', async () => {
    const calls: [string, RequestInit | undefined][] = []
    const fetchFn = (async (url: string, init?: RequestInit) => {
      calls.push([url, init])
      return new Response(JSON.stringify({ buyers: [seen()] }), { status: 200 })
    }) as typeof fetch
    expect(await fetchBuyers(settings, SHOW, ['lani808'], fetchFn)).toEqual({ buyers: [seen()] })
    const [url, init] = calls[0]!
    expect(url).toBe('https://shop.example/api/ingest/buyers')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer k')
    expect(JSON.parse(init?.body as string)).toEqual({ show: SHOW, names: ['lani808'] })
  })

  it('is null when not connected, with nobody to ask about, offline, or on a web app that is older than this', async () => {
    const never = (async () => {
      throw new Error('should not be called')
    }) as typeof fetch
    expect(await fetchBuyers({ ...settings, apiKey: '' }, SHOW, ['a'], never)).toBeNull()
    expect(await fetchBuyers(settings, SHOW, [], never)).toBeNull()
    const old = (async () => new Response('', { status: 404 })) as typeof fetch
    expect(await fetchBuyers(settings, SHOW, ['a'], old)).toBeNull()
    const offline = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    expect(await fetchBuyers(settings, SHOW, ['a'], offline)).toBeNull()
    const odd = (async () => new Response(JSON.stringify({ buyers: 'nope' }), { status: 200 })) as typeof fetch
    expect(await fetchBuyers(settings, undefined, ['a'], odd)).toBeNull()
  })

  it('drops anything that is not a buyer row', async () => {
    const odd = (async () =>
      new Response(JSON.stringify({ buyers: [seen(), { name: 'x', flag: 'enemy', firstTime: true, shows: 0, items: 0 }, { name: 'y' }, null] }), { status: 200 })) as typeof fetch
    expect(await fetchBuyers(settings, undefined, ['a'], odd)).toEqual({ buyers: [seen()] })
  })
})
