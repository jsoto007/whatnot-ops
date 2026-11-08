/**
 * The seller dashboard's chat (whatnot.com/dashboard/live/<id>), shape read from the
 * real page on 2026-11-06: a react-virtuoso list whose rows are bare `<div data-index>`
 * with no classes. The message text here is made up.
 */
import { Recorder, type RecorderEvent } from '../src/capture/recorder'
import { autoDetectRows, buildSelector, chatContainer, findRow, nests } from '../src/capture/row'

function row(i: number, user: string, text: string, badge = ''): string {
  return (
    `<div data-index="${i}" data-item-index="${i}" data-known-size="52" style="overflow-anchor: none;">` +
    `<div class="pb-2"><div class="flex max-w-full items-start gap-2 pl-2 w-full" data-testid="seller-live-chat-message">` +
    `<button class="shrink-0 cursor-pointer bg-transparent p-0"><div class="w-(--size) flex items-center justify-center">` +
    `<span class="flex flex-col relative aspect-square size-8"><img class="rounded-full"></span></div></button>` +
    `<div class="flex min-w-0 flex-col flex-1 pr-2.5"><div class="flex min-w-0 items-center gap-1">` +
    `<button class="min-w-0 truncate bg-transparent p-0 text-left hover:underline"><strong class="text-current block font-semibold truncate">${user}</strong></button>` +
    (badge ? `<div class="flex items-center"><span class="badge">${badge}</span></div>` : '') +
    `</div><strong class="text-current block font-regular my-1 wrap-break-word">${text}</strong></div></div></div></div>`
  )
}

function dashboardChat(rows: string[]): { scroller: HTMLElement; list: HTMLElement } {
  document.body.innerHTML =
    `<section class="flex min-h-0 flex-1 flex-col overflow-hidden"><div class="relative mb-2 min-h-0 flex-1 overflow-hidden rounded-8">` +
    `<button>Scroll to Bottom</button>` +
    `<div class="h-full" data-testid="virtuoso-scroller" data-virtuoso-scroller="true" tabindex="0">` +
    `<div data-viewport-type="element"><div data-testid="virtuoso-item-list">${rows.join('')}</div></div></div></div></section>`
  return {
    scroller: document.querySelector('[data-testid="virtuoso-scroller"]')!,
    list: document.querySelector('[data-testid="virtuoso-item-list"]')!,
  }
}

const three = [
  row(0, 'host_shop', 'shared this show', 'Host'),
  row(1, 'host_shop', 'joined'),
  row(2, 'viewer_one', 'joined'),
]

const flush = () => new Promise((r) => setTimeout(r, 0))

function collect(rec: Recorder): string[] {
  const texts: string[] = []
  rec.on((e: RecorderEvent) => e.type === 'row' && texts.push(e.row.rawText))
  return texts
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('Whatnot seller dashboard chat', () => {
  it('builds a selector that matches only the rows, not the boxes inside them', () => {
    const { list } = dashboardChat(three)
    const clicked = list.querySelectorAll('strong')[3]! // "joined" of the second row
    const found = findRow(clicked, list.parentElement!.parentElement!)
    expect(found).toBe(list.children[1])
    const selector = buildSelector(found!)
    expect(selector).not.toBe('div')
    expect([...document.querySelectorAll(selector)]).toEqual([...list.children])
    expect(nests(document.body, selector)).toBe(false)
  })

  it('recognises a click when the chat has only one or two messages (before the show)', () => {
    for (const rows of [three.slice(0, 1), three.slice(0, 2)]) {
      const { list } = dashboardChat(rows)
      const clicked = list.lastElementChild!.querySelectorAll('strong')[1]!
      // The chat does not scroll yet, so the box is the page: the click still finds its row.
      const found = findRow(clicked, document.body)
      expect(found).toBe(list.lastElementChild)
      expect([...document.querySelectorAll(buildSelector(found!))]).toEqual([...list.children])
    }
  })

  it('records each message once', async () => {
    const { list } = dashboardChat(three)
    const sample = list.children[0]!
    const rec = new Recorder('show1', { now: () => 1_700_000_000_000 })
    const texts = collect(rec)
    rec.start(chatContainer(sample), buildSelector(sample))
    expect(texts).toEqual(['host_shop Host shared this show', 'host_shop joined', 'viewer_one joined'])
    list.insertAdjacentHTML('beforeend', row(3, 'viewer_two', 'I want the red one'))
    await flush()
    expect(texts).toHaveLength(4)
    expect(texts[3]).toBe('viewer_two I want the red one')
    rec.stop()
  })

  it('a bare tag selector saved by an older version is not used again', () => {
    dashboardChat(three)
    expect(nests(document.body, 'div')).toBe(true)
    const rec = new Recorder('show1', { now: () => 1_700_000_000_000 })
    const texts = collect(rec)
    const events: string[] = []
    rec.on((e) => events.push(e.type))
    rec.resume(document, ['div'])
    expect(texts).toEqual([])
    expect(events).toContain('lost')
    rec.stop()
  })

  it('falls back from an old bare selector to a safe one saved with it', () => {
    const { list } = dashboardChat(three)
    const good = buildSelector(list.children[0]!)
    const rec = new Recorder('show1', { now: () => 1_700_000_000_000 })
    const texts = collect(rec)
    rec.resume(document, ['div', good])
    expect(texts).toHaveLength(3)
    expect(rec.currentSelector).toBe(good)
    rec.stop()
  })

  it('auto-detect (no click) picks the rows, not nested pieces', () => {
    const { scroller } = dashboardChat([...three, row(3, 'viewer_two', 'hi')])
    const found = autoDetectRows(scroller)
    expect(found).not.toBeNull()
    expect(nests(scroller, found!.selector)).toBe(false)
    expect(found!.count).toBe(4)
  })
})
