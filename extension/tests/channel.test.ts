import { CHANNEL_SOURCES, channelOfPath, cleanChannel, readChannel, sameChannel } from '../src/capture/channel'
import { chat, page, sellerLink, sellerName, winnerFooter } from './fixtures/channel'

// Made-up names only. Where whatnot.com shows the seller's name is NOT verified: these tests
// pin how the reader treats the shapes in its own table, not that the table matches Whatnot.

function doc(html: string): Document {
  const d = document.implementation.createHTMLDocument('live')
  d.body.innerHTML = html
  return d
}

describe('readChannel', () => {
  it("reads the seller's name from the first source in the table", () => {
    expect(readChannel(doc(page(sellerName('maple_cards_demo'))), '/live/made-up')).toBe('maple_cards_demo')
  })

  it("reads the handle from the seller's profile link, not the link's words", () => {
    expect(readChannel(doc(page(sellerLink('maple_cards_demo', 'Maple Cards ✨'))), '/live/made-up')).toBe('maple_cards_demo')
  })

  it('takes the handle in the address of a channel page first', () => {
    expect(readChannel(doc(page(sellerName('maple_cards_demo'))), '/user/maple_cards_demo')).toBe('maple_cards_demo')
    expect(channelOfPath('/user/maple_cards_demo/shows')).toBe('maple_cards_demo')
    expect(channelOfPath('/user/')).toBe('')
    expect(channelOfPath('/live/made-up')).toBe('')
    expect(channelOfPath('/user/%E0%A4%A')).toBe('') // a broken escape is not a name
  })

  it('reads nothing when the page shows no seller: recording then goes on as before', () => {
    expect(readChannel(doc(''), '/live/made-up')).toBe('')
    expect(readChannel(doc(page('')), '/live/made-up')).toBe('')
    expect(readChannel(doc('<span data-testid="show-host-username"></span>'), '/')).toBe('')
  })

  it('never reads a winner or a person in the chat as the seller', () => {
    expect(readChannel(doc(page(winnerFooter('fern_buyer'), chat('oak_viewer', 'pine_bidder'))), '/live/made-up')).toBe('')
    expect(readChannel(doc(page(sellerName('maple_cards_demo') + winnerFooter('fern_buyer'))), '/live/made-up')).toBe('maple_cards_demo')
  })

  it('reads nothing from what it is told to skip (the chat box, the panel)', () => {
    const d = doc(page(sellerName('maple_cards_demo')))
    const name = d.querySelector('[data-testid="show-host-username"]')!
    expect(readChannel(d, '/live/made-up', (n) => n === name)).toBe('')
  })

  it('reads nothing when the page names two different sellers', () => {
    const d = doc(page(sellerName('maple_cards_demo') + sellerName('birch_breaks_demo')))
    expect(readChannel(d, '/live/made-up')).toBe('')
    const same = doc(page(sellerName('maple_cards_demo') + sellerName('@Maple_Cards_Demo')))
    expect(readChannel(same, '/live/made-up')).toBe('maple_cards_demo')
  })

  it('reads nothing that is not shaped like a handle', () => {
    for (const words of ['Follow us', 'Maple Cards', 'x', 'a'.repeat(81), '<b>', '']) {
      expect(readChannel(doc(page(sellerName(words))), '/live/made-up'), words).toBe('')
    }
  })

  it('is one small table', () => {
    expect(CHANNEL_SOURCES.length).toBeGreaterThan(0)
    expect(CHANNEL_SOURCES.length).toBeLessThanOrEqual(6)
    for (const s of CHANNEL_SOURCES) expect(() => document.querySelector(s.css)).not.toThrow()
  })
})

describe('cleanChannel and sameChannel', () => {
  it('trims, drops one leading @ and keeps only a handle', () => {
    expect(cleanChannel('  @maple_cards_demo ')).toBe('maple_cards_demo')
    expect(cleanChannel('maple.cards-demo')).toBe('maple.cards-demo')
    expect(cleanChannel('two words')).toBe('')
    expect(cleanChannel(undefined)).toBe('')
  })

  it('compares without case and without the @', () => {
    expect(sameChannel('Maple_Cards_Demo', '@maple_cards_demo')).toBe(true)
    expect(sameChannel('maple_cards_demo', 'birch_breaks_demo')).toBe(false)
  })
})
