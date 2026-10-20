import { cleanText, leafSegments, rowText, splitRow } from '../src/capture/text'

describe('cleanText', () => {
  it('collapses newlines to " | " exactly like v1.2', () => {
    expect(cleanText('lani808\n  sold 2 \n')).toBe('lani808 | sold 2')
    expect(cleanText(null)).toBe('')
  })
})

describe('leafSegments / splitRow', () => {
  function row(html: string): Element {
    const div = document.createElement('div')
    div.innerHTML = html
    return div
  }

  it('takes the first segment as username when it looks like one', () => {
    const el = row('<span class="u">lani808</span><span class="t">I will take 2 please</span>')
    const raw = rowText(el)
    expect(splitRow(leafSegments(el), raw)).toEqual({ username: 'lani808', body: 'I will take 2 please' })
  })

  it('strips a trailing colon from the username element', () => {
    const el = row('<b>pokemom_22:</b> <i>mine!</i>')
    expect(splitRow(leafSegments(el), rowText(el))).toEqual({ username: 'pokemom_22', body: 'mine!' })
  })

  it('falls back to "name: text" in flat text', () => {
    expect(splitRow(['cardguy: sold to me'], 'cardguy: sold to me')).toEqual({
      username: 'cardguy',
      body: 'sold to me',
    })
  })

  it('keeps the whole line as body when no username can be found', () => {
    expect(splitRow(['Auction ended'], 'Auction ended')).toEqual({ username: '', body: 'Auction ended' })
    expect(splitRow(['has spaces', 'text'], 'has spaces text')).toEqual({ username: '', body: 'has spaces text' })
  })

  it('joins a body split across several elements (emoji images between text)', () => {
    const el = row('<span>buyer1</span><span>take</span><img alt=""><span>two</span>')
    expect(splitRow(leafSegments(el), rowText(el))).toEqual({ username: 'buyer1', body: 'take two' })
  })
})
