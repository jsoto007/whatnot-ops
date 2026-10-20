import { autoDetectRows, buildSelector, findRow, sig } from '../src/capture/row'

function chat(rowClass = 'msg', n = 5): { box: HTMLElement; rows: HTMLElement[] } {
  const box = document.createElement('div')
  box.className = 'chat'
  const list = document.createElement('ul')
  list.className = 'list'
  box.appendChild(list)
  const rows: HTMLElement[] = []
  for (let i = 0; i < n; i++) {
    const li = document.createElement('li')
    li.className = rowClass
    li.innerHTML = `<span class="u">user${i}</span><span class="t">message ${i}</span>`
    list.appendChild(li)
    rows.push(li)
  }
  document.body.appendChild(box)
  return { box, rows }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('findRow', () => {
  it('climbs from the clicked text to the ancestor with the most look-alike siblings', () => {
    const { box, rows } = chat()
    const target = rows[2]!.querySelector('.t')!
    expect(findRow(target, box)).toBe(rows[2])
  })

  it('returns null when fewer than three siblings share the shape', () => {
    const { box, rows } = chat('msg', 2)
    expect(findRow(rows[0]!.querySelector('.t')!, box)).toBeNull()
  })
})

describe('buildSelector / sig', () => {
  it('uses tag plus all classes', () => {
    const el = document.createElement('div')
    el.className = 'a b-c'
    expect(buildSelector(el)).toBe('div.a.b-c')
    expect(sig(el)).toBe('DIV|a b-c')
  })
})

describe('autoDetectRows', () => {
  it('finds the row selector with no click', () => {
    const { box } = chat('row-v2', 8)
    const found = autoDetectRows(box)
    expect(found?.selector).toBe('li.row-v2')
    expect(found?.count).toBe(8)
  })

  it('prefers classed groups over a bare tag group of the same size', () => {
    const box = document.createElement('div')
    box.innerHTML =
      '<div><p>a</p><p>b</p><p>c</p><p>d</p></div>' +
      '<section><div class="m">x</div><div class="m">y</div><div class="m">z</div></section>'
    document.body.appendChild(box)
    expect(autoDetectRows(box)?.selector).toBe('div.m')
  })

  it('returns null when nothing repeats', () => {
    const box = document.createElement('div')
    box.innerHTML = '<p>one</p><span>two</span>'
    expect(autoDetectRows(box)).toBeNull()
  })
})
