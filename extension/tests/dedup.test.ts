import { Deduper } from '../src/capture/dedup'

describe('Deduper', () => {
  it('records an element only once', () => {
    const d = new Deduper()
    const el = document.createElement('div')
    expect(d.seenElement(el)).toBe(false)
    expect(d.seenElement(el)).toBe(true)
  })

  it('treats the same text within 5s as a re-render, later as a real repeat', () => {
    const d = new Deduper(5000)
    d.remember('mine', 1000)
    expect(d.isDuplicate('mine', 3000, false)).toBe(true)
    expect(d.isDuplicate('mine', 6001, false)).toBe(false)
  })

  it('skips known tail texts only during an initial scan', () => {
    const d = new Deduper()
    d.setKnownTail(['old msg'])
    expect(d.isDuplicate('old msg', 0, true)).toBe(true)
    expect(d.isDuplicate('old msg', 0, false)).toBe(false)
    expect(d.isDuplicate('new msg', 0, true)).toBe(false)
  })
})

describe('Deduper.replayPrefix', () => {
  function withHistory(...texts: string[]) {
    const d = new Deduper()
    texts.forEach((t, i) => d.remember(t, i))
    return d
  }

  it('recognises a batch that replays recorded rows in order', () => {
    const d = withHistory('a', 'b', 'c', 'd', 'e')
    expect(d.replayPrefix(['c', 'd', 'e'])).toBe(3)
    expect(d.replayPrefix(['a', 'b', 'c', 'd', 'e', 'NEW'])).toBe(5)
  })

  it('does not treat a burst of new messages as a replay', () => {
    const d = withHistory('mine', 'sold', 'mine', 'next item')
    expect(d.replayPrefix(['mine', 'mine', 'mine'])).toBe(0) // not contiguous in history
    expect(d.replayPrefix(['x', 'y', 'z'])).toBe(0)
    expect(d.replayPrefix(['mine', 'sold'])).toBe(0) // fewer than three
  })

  it('uses the resumed tail as history too', () => {
    const d = new Deduper()
    d.setKnownTail(['p', 'q', 'r'])
    expect(d.replayPrefix(['p', 'q', 'r', 's'])).toBe(3)
  })
})
