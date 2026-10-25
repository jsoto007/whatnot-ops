import { Panel, clampLayout, readLayout } from '../src/content/panel'

const item = (code: string, name: string, available: number, skuId = 1) => ({ skuId, code, name, available })

describe('panel low-stock list', () => {
  it('lists every item in full, fewest first, with no "more on the Inventory page" cut-off', () => {
    const p = new Panel(document)
    const items = Array.from({ length: 8 }, (_, i) => item(`C-${i}`, `$100 rug - black & grey version ${i}`, 3 - (i % 4), i))
    p.setLowStock(items)
    const low = p.el('low')
    expect(low.hidden).toBe(false)
    expect(low.querySelector('.lt')!.textContent).toBe('Running low in this show')
    const rows = [...low.querySelectorAll('.li')]
    expect(rows).toHaveLength(8)
    expect(rows[0]!.querySelector('.nm')!.textContent).toBe('$100 rug - black & grey version 3C-3')
    expect(rows[0]!.querySelector('.cd')!.textContent).toBe('C-3')
    expect(rows[0]!.querySelector('.n')!.textContent).toBe('sold out')
    expect(low.textContent).not.toContain('more on the Inventory page')
  })

  it('hides when the show has nothing low', () => {
    const p = new Panel(document)
    p.setLowStock([item('A', 'A', 1)])
    p.setLowStock([])
    expect(p.el('low').hidden).toBe(true)
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
    p.el('min').click()
    expect(p.el('p').style.height).toBe('500px')
  })

  it('stays inside the window however it was dragged or sized', () => {
    expect(clampLayout({ x: -200, y: -50 }, 1280, 800)).toEqual({ x: 8, y: 8 })
    expect(clampLayout({ x: 5000, y: 5000 }, 1280, 800)).toEqual({ x: 1280 - 336 - 8, y: 800 - 70 - 8 })
    expect(clampLayout({ x: 100, y: 100, w: 5000, h: 5000 }, 1280, 800)).toEqual({ x: 8, y: 100, w: 1264, h: 692 })
    expect(clampLayout({ x: 100, y: 100, w: 10, h: 10 }, 1280, 800)).toEqual({ x: 100, y: 100, w: 280, h: 140 })
  })

  it('reads back only a real layout from storage', () => {
    expect(readLayout({ x: 1, y: 2, w: 300 })).toEqual({ x: 1, y: 2, w: 300 })
    expect(readLayout({ x: 1, y: 'a' })).toBeNull()
    expect(readLayout(null)).toBeNull()
    expect(readLayout({ x: 1, y: 2, w: NaN })).toEqual({ x: 1, y: 2 })
  })
})
