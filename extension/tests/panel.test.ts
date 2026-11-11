import { NOTICE_MAX, NOTICE_MS, Panel, clampLayout, readLayout } from '../src/content/panel'

const item = (code: string, name: string, available: number, skuId = 1) => ({ skuId, code, name, available })
const notices = (p: Panel) => [...p.el('toasts').children] as HTMLElement[]
const titles = (p: Panel) => notices(p).map((t) => t.querySelector('b')!.textContent)

describe('panel running low', () => {
  it('one row says how many; it opens the list, every item in full, fewest first', () => {
    const p = new Panel(document)
    const items = Array.from({ length: 8 }, (_, i) => item(`C-${i}`, `$100 rug - black & grey version ${i}`, 3 - (i % 4), i))
    p.setLowStock(items)
    const btn = p.el('lowbtn')
    const low = p.el('low')
    expect(btn.hidden).toBe(false)
    expect(p.el('lown').textContent).toBe('8 running low')
    expect(btn.classList.contains('urgent')).toBe(true)
    expect(low.hidden).toBe(true) // closed until asked
    btn.click()
    expect(low.hidden).toBe(false)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    const rows = [...low.querySelectorAll('.li')]
    expect(rows).toHaveLength(8)
    expect(rows[0]!.querySelector('.nm')!.textContent).toBe('$100 rug - black & grey version 3C-3')
    expect(rows[0]!.querySelector('.cd')!.textContent).toBe('C-3')
    expect(rows[0]!.querySelector('.n')!.textContent).toBe('sold out')
    expect(low.textContent).not.toContain('more on the Inventory page')
    // The next poll keeps it open and recounts.
    p.setLowStock(items.slice(0, 2))
    expect(low.hidden).toBe(false)
    expect(p.el('lown').textContent).toBe('2 running low')
    expect(btn.classList.contains('urgent')).toBe(false)
  })

  it('hides when the show has nothing low', () => {
    const p = new Panel(document)
    p.setLowStock([item('A', 'A', 1)])
    p.setLowStock([])
    expect(p.el('lowbtn').hidden).toBe(true)
    expect(p.el('low').hidden).toBe(true)
  })
})

describe('panel notifications', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('shows a notice for five seconds, then fades it out', () => {
    const p = new Panel(document)
    p.notify({ kind: 'win', title: 'tiffybid won · $19.00', detail: '$1 BEAUTY PULLS #1290' })
    const [n] = notices(p)
    expect(n!.textContent).toBe('tiffybid won · $19.00$1 BEAUTY PULLS #1290')
    expect(n!.className).toBe('toast glass win')
    vi.advanceTimersByTime(NOTICE_MS - 1)
    expect(n!.classList.contains('gone')).toBe(false)
    vi.advanceTimersByTime(1)
    expect(n!.classList.contains('gone')).toBe(true)
    vi.advanceTimersByTime(500)
    expect(notices(p)).toHaveLength(0)
  })

  it('a line learnt a moment later joins the notice while it shows, and not once it is going', () => {
    const p = new Panel(document)
    const n = p.notify({ kind: 'win', title: 'lani808 won · $25.00', detail: 'Lot #7' })
    p.amend(n, 'Back again · 12 items over 5 shows')
    expect(n.textContent).toBe('lani808 won · $25.00Lot #7Back again · 12 items over 5 shows')
    expect(n.querySelector('.more')!.textContent).toBe('Back again · 12 items over 5 shows')
    vi.advanceTimersByTime(NOTICE_MS)
    p.amend(n, 'too late')
    expect(n.textContent).not.toContain('too late')
  })

  it("the panel's own line about the latest winner hides when there is nothing to say", () => {
    const p = new Panel(document)
    expect(p.el('who').hidden).toBe(true)
    p.setBuyer('lani808: VIP · back again · 12 items over 5 shows')
    expect(p.el('who').hidden).toBe(false)
    expect(p.el('who').textContent).toBe('lani808: VIP · back again · 12 items over 5 shows')
    p.setBuyer('')
    expect(p.el('who').hidden).toBe(true)
  })

  it('a click takes a notice away; beyond three the oldest goes first', () => {
    const p = new Panel(document)
    for (let i = 0; i < NOTICE_MAX + 1; i++) p.notify({ kind: 'low', title: `${i} left`, detail: 'Mug' })
    expect(titles(p)).toEqual(['1 left', '2 left', '3 left'])
    notices(p)[1]!.click()
    vi.advanceTimersByTime(500)
    expect(titles(p)).toEqual(['1 left', '3 left'])
  })

  it('a notice the pointer rests on waits', () => {
    const p = new Panel(document)
    p.notify({ kind: 'win', title: 'x won · $1.00', detail: 'Lot' })
    const [n] = notices(p)
    n!.dispatchEvent(new Event('pointerenter'))
    vi.advanceTimersByTime(NOTICE_MS * 3)
    expect(notices(p)).toHaveLength(1)
    n!.dispatchEvent(new Event('pointerleave'))
    vi.advanceTimersByTime(3000)
    expect(notices(p)).toHaveLength(0)
  })

  it('stock alerts become amber and red notices, which Stop clears together', () => {
    const p = new Panel(document)
    p.showStockAlerts([
      { item: item('MUG', 'Mug', 2), text: '2 left: Mug (MUG)', urgent: false },
      { item: item('CAP', 'Cap', 0, 2), text: 'sold out: Cap (CAP)', urgent: true },
    ])
    const [a, b] = notices(p)
    expect(a!.className).toBe('toast glass low')
    expect(a!.textContent).toBe('2 leftMug · MUG')
    expect(b!.className).toBe('toast glass out urgent')
    expect(b!.textContent).toBe('Sold outCap · CAP')
    p.notify({ kind: 'win', title: 'x won · $1.00', detail: 'Lot' })
    p.clearStockAlert()
    expect(titles(p)).toEqual(['x won · $1.00'])
  })

  it('taking the panel off the page stops every timer', () => {
    const p = new Panel(document)
    p.mount(document)
    p.notify({ kind: 'win', title: 'x won · $1.00', detail: 'Lot' })
    p.unmount()
    expect(() => vi.advanceTimersByTime(NOTICE_MS * 2)).not.toThrow()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('panel minimised, tabs and menu', () => {
  it('minimising leaves the pill with the dot and the count; the pill opens it again', () => {
    const p = new Panel(document)
    p.setCount(2128)
    p.setDot('good')
    expect(p.el('pill').hidden).toBe(true)
    p.el('min').click()
    expect(p.isMinimised).toBe(true)
    expect(p.el('p').hidden).toBe(true)
    expect(p.el('pill').hidden).toBe(false)
    expect(p.el('pcnt').textContent).toBe('2128')
    expect(p.el('pdot').className).toBe('dot good')
    p.setDot('bad')
    expect(p.el('pdot').className).toBe('dot bad')
    p.el('pill').click()
    expect(p.isMinimised).toBe(false)
    expect(p.el('p').hidden).toBe(false)
    expect(p.el('pill').hidden).toBe(true)
  })

  it('the click that ends a drag of the pill does not open the panel', () => {
    const p = new Panel(document)
    p.el('min').click()
    p.el('pill').dataset.dragged = '1'
    p.el('pill').click()
    expect(p.isMinimised).toBe(true)
    p.el('pill').click()
    expect(p.isMinimised).toBe(false)
  })

  it('the dot states map to colours', () => {
    const p = new Panel(document)
    for (const [state, cls] of [
      ['idle', 'dot'],
      ['picking', 'dot pick'],
      ['good', 'dot good'],
      ['warn', 'dot warn'],
      ['bad', 'dot bad'],
    ] as const) {
      p.setDot(state)
      expect(p.el('dot').className).toBe(cls)
    }
  })

  it('one tab at a time, and the Flags tab counts', () => {
    const p = new Panel(document)
    expect(p.el('pane-live').hidden).toBe(false)
    expect(p.el('pane-find').hidden).toBe(true)
    p.el('tab-find').click()
    expect(p.el('pane-live').hidden).toBe(true)
    expect(p.el('pane-find').hidden).toBe(false)
    expect(p.el('tab-find').getAttribute('aria-selected')).toBe('true')
    expect(p.el('tab-live').getAttribute('aria-selected')).toBe('false')
    p.el('tab-flags').click()
    expect(p.el('pane-find').hidden).toBe(true)
    expect(p.el('pane-flags').hidden).toBe(false)
    p.setFlagCount(3)
    expect(p.el('nflags').hidden).toBe(false)
    expect(p.el('nflags').textContent).toBe('3')
    p.setFlagCount(0)
    expect(p.el('nflags').hidden).toBe(true)
  })

  it('the menu opens from ⋯ and closes on a choice, on Escape or on a click elsewhere', () => {
    const p = new Panel(document)
    expect(p.el('menubox').hidden).toBe(true)
    p.el('menu').click()
    expect(p.el('menubox').hidden).toBe(false)
    expect(p.el('menu').getAttribute('aria-expanded')).toBe('true')
    p.el('backup').click()
    expect(p.el('menubox').hidden).toBe(true)
    p.el('menu').click()
    p.el('st').click()
    expect(p.el('menubox').hidden).toBe(true)
    p.el('menu').click()
    p.el('st').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(p.el('menubox').hidden).toBe(true)
  })
})

describe('panel layout', () => {
  it('starts in the bottom-right corner', () => {
    const p = new Panel(document)
    expect(p.host.style.right).toBe('16px')
    expect(p.host.style.bottom).toBe('16px')
    expect(p.getLayout()).toBeNull()
  })

  it('goes where it is put, at the size it was given, and back to the corner on reset', () => {
    const p = new Panel(document)
    p.setLayout({ x: 40, y: 50, w: 400, h: 500 })
    expect([p.host.style.left, p.host.style.top]).toEqual(['40px', '50px'])
    expect([p.el('p').style.width, p.el('p').style.height]).toEqual(['400px', '500px'])
    p.setLayout(null)
    expect(p.host.style.right).toBe('16px')
    expect(p.el('p').style.width).toBe('')
  })

  it('a minimised panel ignores the resized height', () => {
    const p = new Panel(document)
    p.setLayout({ x: 40, y: 50, w: 400, h: 500 })
    p.el('min').click()
    expect(p.el('p').style.height).toBe('')
    p.el('pill').click()
    expect(p.el('p').style.height).toBe('500px')
  })

  it('notices stack above the panel, or below it near the top of the window', () => {
    const p = new Panel(document)
    expect(p.el('toasts').classList.contains('below')).toBe(false)
    p.setLayout({ x: 40, y: 50 })
    expect(p.el('toasts').classList.contains('below')).toBe(true)
    p.setLayout({ x: 40, y: 400 })
    expect(p.el('toasts').classList.contains('below')).toBe(false)
  })

  it('stays inside the window however it was dragged or sized', () => {
    expect(clampLayout({ x: -200, y: -50 }, 1280, 800)).toEqual({ x: 8, y: 8 })
    expect(clampLayout({ x: 5000, y: 5000 }, 1280, 800)).toEqual({ x: 1280 - 336 - 8, y: 800 - 70 - 8 })
    expect(clampLayout({ x: 100, y: 100, w: 5000, h: 5000 }, 1280, 800)).toEqual({ x: 8, y: 100, w: 1264, h: 692 })
    expect(clampLayout({ x: 100, y: 100, w: 10, h: 10 }, 1280, 800)).toEqual({ x: 100, y: 100, w: 280, h: 140 })
    // Minimised, only the pill has to stay on screen; the panel's own width is kept for later.
    expect(clampLayout({ x: 5000, y: 100, w: 400 }, 1280, 800, 72)).toEqual({ x: 1280 - 72 - 8, y: 100, w: 400 })
  })

  it('reads back only a real layout from storage', () => {
    expect(readLayout({ x: 1, y: 2, w: 300 })).toEqual({ x: 1, y: 2, w: 300 })
    expect(readLayout({ x: 1, y: 'a' })).toBeNull()
    expect(readLayout(null)).toBeNull()
    expect(readLayout({ x: 1, y: 2, w: NaN })).toEqual({ x: 1, y: 2 })
  })
})
