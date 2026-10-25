import { Recorder, type RecorderEvent } from '../src/capture/recorder'

function makeChat(): { box: HTMLElement; list: HTMLElement } {
  const box = document.createElement('div')
  box.className = 'chat'
  const list = document.createElement('div')
  list.className = 'list'
  box.appendChild(list)
  document.body.appendChild(box)
  return { box, list }
}

function addRow(list: HTMLElement, user: string, text: string, rowClass = 'msg'): HTMLElement {
  const div = document.createElement('div')
  div.className = rowClass
  div.innerHTML = `<span class="u">${user}</span><span class="t">${text}</span>`
  list.appendChild(div)
  return div
}

const flush = () => new Promise((r) => setTimeout(r, 0))

let now = 1_700_000_000_000
beforeEach(() => {
  now = 1_700_000_000_000
  document.body.innerHTML = ''
})

function collect(rec: Recorder): RecorderEvent[] {
  const events: RecorderEvent[] = []
  rec.on((e) => events.push(e))
  return events
}

describe('Recorder', () => {
  it('records existing rows on start and new rows as they are appended', async () => {
    const { box, list } = makeChat()
    addRow(list, 'a', 'hello')
    addRow(list, 'b', 'world')
    const rec = new Recorder('show1', { now: () => now })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    expect(events.map((e) => e.type === 'row' && e.row.body)).toEqual(['hello', 'world'])

    now += 1000
    addRow(list, 'c', 'third')
    await flush()
    expect(rec.rowCount).toBe(3)
    const last = events.at(-1)
    expect(last?.type === 'row' && last.row).toMatchObject({
      showId: 'show1',
      username: 'c',
      body: 'third',
      rawText: 'c third',
      seq: 3,
      capturedAt: new Date(now).toISOString(),
    })
    rec.stop()
  })

  it('ignores a re-rendered copy of a recent message but keeps a real repeat later', async () => {
    const { box, list } = makeChat()
    const rec = new Recorder('s', { now: () => now })
    rec.start(box, 'div.msg')
    addRow(list, 'a', 'mine')
    await flush()
    now += 1000
    const again = addRow(list, 'a', 'mine') // Whatnot redrawing the same node
    await flush()
    expect(rec.rowCount).toBe(1)
    again.remove()
    now += 10_000
    addRow(list, 'a', 'mine') // said again 10s later
    await flush()
    expect(rec.rowCount).toBe(2)
    rec.stop()
  })

  it('skips rows already saved when resuming', () => {
    const { box, list } = makeChat()
    addRow(list, 'a', 'old one')
    addRow(list, 'b', 'new one')
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.start(box, 'div.msg', 7, ['a old one'])
    expect(events).toHaveLength(1)
    expect(events[0]?.type === 'row' && events[0].row.seq).toBe(8)
    rec.stop()
  })

  it('ignores nodes the caller marks as its own', async () => {
    const { box, list } = makeChat()
    const panel = document.createElement('div')
    panel.className = 'msg'
    panel.textContent = 'our panel'
    const rec = new Recorder('s', { now: () => now, ignore: (n) => n === panel || panel.contains(n) })
    rec.start(box, 'div.msg')
    list.appendChild(panel)
    addRow(list, 'a', 'real')
    await flush()
    expect(rec.rowCount).toBe(1)
    rec.stop()
  })

  it('heals when the rows get new class names', async () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    const rec = new Recorder('s', { now: () => now, staleMs: 1000, staleMutations: 3 })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    expect(rec.rowCount).toBe(3)

    // Whatnot re-renders: every row gets a new class, new rows use it too.
    for (const r of list.querySelectorAll('div.msg')) r.className = 'row-v2'
    for (let i = 3; i < 8; i++) addRow(list, 'u' + i, 'm' + i, 'row-v2')
    await flush()
    expect(rec.rowCount).toBe(3) // nothing matched the old selector

    now += 2000
    rec.check()
    expect(events.some((e) => e.type === 'healed' && e.selector === 'div.row-v2')).toBe(true)
    expect(rec.currentSelector).toBe('div.row-v2')
    expect(rec.rowCount).toBe(8) // the five missed rows were picked up, the three old ones were not repeated

    addRow(list, 'u9', 'm9', 'row-v2')
    await flush()
    expect(rec.rowCount).toBe(9)
    rec.stop()
  })

  it('follows the chat when the whole container is replaced', async () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.start(list, 'div.msg')

    box.remove()
    const fresh = makeChat()
    for (let i = 0; i < 4; i++) addRow(fresh.list, 'n' + i, 'x' + i)
    rec.check()
    expect(events.some((e) => e.type === 'healed')).toBe(true)
    expect(rec.rowCount).toBe(7)
    addRow(fresh.list, 'n9', 'x9')
    await flush()
    expect(rec.rowCount).toBe(8)
    rec.stop()
  })

  it('reports lost when nothing row-like can be found', () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    const rec = new Recorder('s', { now: () => now, staleMs: 1000, staleMutations: 1 })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    list.innerHTML = '<p>chat is closed</p>'
    return flush().then(() => {
      now += 5000
      rec.check()
      expect(events.at(-1)).toEqual({ type: 'lost', reason: expect.any(String) })
      rec.stop()
    })
  })
})

describe('Recorder keeps recording until stopped', () => {
  it('a quiet chat with other things moving is not lost', async () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    const rec = new Recorder('s', { now: () => now, staleMs: 1000, staleMutations: 3 })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    // Reactions / banners keep getting added, no new message for a while.
    for (let i = 0; i < 10; i++) {
      const fx = document.createElement('i')
      fx.className = 'heart'
      box.appendChild(fx)
    }
    await flush()
    for (let i = 0; i < 5; i++) {
      now += 2000
      rec.check()
    }
    expect(events.filter((e) => e.type !== 'row')).toEqual([])
    addRow(list, 'late', 'still here')
    await flush()
    expect(rec.rowCount).toBe(4)
    rec.stop()
  })

  it('waits while the chat is gone instead of latching onto another list, then carries on', async () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    // Something else on the page that looks like a list (product cards).
    const shop = document.createElement('div')
    for (let i = 0; i < 6; i++) shop.innerHTML += `<div class="card">Item ${i}</div>`
    document.body.appendChild(shop)
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.start(list, 'div.msg')

    box.remove() // the chat unmounts (tab switch, reconnect)
    rec.check()
    rec.check()
    expect(events.filter((e) => e.type === 'lost')).toHaveLength(1) // said once, not every tick
    expect(rec.attached).toBe(false)
    expect(rec.running).toBe(true)
    expect(rec.currentSelector).toBe('div.msg')

    // It comes back as a new container: old lines redrawn plus a new one.
    const back = makeChat()
    for (let i = 0; i < 3; i++) addRow(back.list, 'u' + i, 'm' + i)
    addRow(back.list, 'new', 'while it was gone')
    rec.check()
    expect(events.at(-1)).toEqual({ type: 'healed', selector: 'div.msg' })
    expect(rec.attached).toBe(true)
    expect(rec.rowCount).toBe(4)
    addRow(back.list, 'next', 'after')
    await flush()
    expect(rec.rowCount).toBe(5)
    rec.stop()
  })

  it('carries on when rows come back into the same container', async () => {
    const { box, list } = makeChat()
    for (let i = 0; i < 3; i++) addRow(list, 'u' + i, 'm' + i)
    const rec = new Recorder('s', { now: () => now, staleMs: 1000, staleMutations: 1 })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    list.innerHTML = '<p>chat paused</p>'
    await flush()
    now += 5000
    rec.check()
    expect(events.at(-1)?.type).toBe('lost')
    list.innerHTML = ''
    addRow(list, 'z', 'back again')
    await flush()
    rec.check()
    expect(events.at(-1)).toEqual({ type: 'healed', selector: 'div.msg' })
    expect(rec.rowCount).toBe(4)
    rec.stop()
  })

  it('resume() finds the chat without a click and skips what was saved', () => {
    const { list } = makeChat()
    addRow(list, 'a', 'saved')
    addRow(list, 'b', 'not yet')
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.resume(document, ['div.msg'], 5, ['a saved'])
    expect(rec.attached).toBe(true)
    expect(events.filter((e) => e.type === 'row').map((e) => e.type === 'row' && e.row.seq)).toEqual([6])
    rec.stop()
  })

  it('resume() waits for a chat that has not rendered yet', async () => {
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.resume(document, ['div.msg'], 0, [])
    expect(events).toEqual([{ type: 'lost', reason: expect.any(String) }])
    const { list } = makeChat()
    addRow(list, 'a', 'first')
    rec.check()
    expect(rec.attached).toBe(true)
    expect(rec.rowCount).toBe(1)
    addRow(list, 'b', 'second')
    await flush()
    expect(rec.rowCount).toBe(2)
    rec.stop()
  })

  it('resume() falls back to a row shape the chat used before', () => {
    const { list } = makeChat()
    addRow(list, 'a', 'old classes again')
    const rec = new Recorder('s', { now: () => now })
    rec.resume(document, ['div.row-v2', 'div.msg'], 0, [])
    expect(rec.attached).toBe(true)
    expect(rec.currentSelector).toBe('div.msg')
    expect(rec.rowCount).toBe(1)
    rec.stop()
  })

  it('stop() ends it: no more checks, no more rows', async () => {
    const { box, list } = makeChat()
    const rec = new Recorder('s', { now: () => now })
    const events = collect(rec)
    rec.start(box, 'div.msg')
    rec.stop()
    box.remove()
    rec.check()
    addRow(list, 'a', 'after stop')
    await flush()
    expect(events).toEqual([])
    expect(rec.running).toBe(false)
  })
})

describe('Recorder re-render handling', () => {
  it('skips a full re-render of old rows but records the new message in the same batch', async () => {
    const { box, list } = makeChat()
    const rec = new Recorder('s', { now: () => now })
    rec.start(box, 'div.msg')
    for (let i = 0; i < 6; i++) {
      now += 1000
      addRow(list, 'u' + i, 'm' + i)
      await flush()
    }
    expect(rec.rowCount).toBe(6)
    now += 60_000 // far outside the 5-second text window
    const texts = [...list.children].map((r) => [r.querySelector('.u')!.textContent!, r.querySelector('.t')!.textContent!])
    list.replaceChildren()
    for (const [u, t] of texts) addRow(list, u!, t!)
    addRow(list, 'u9', 'brand new')
    await flush()
    expect(rec.rowCount).toBe(7)
  })

  it('still records three new messages that arrive in one batch', async () => {
    const { box, list } = makeChat()
    const rec = new Recorder('s', { now: () => now })
    rec.start(box, 'div.msg')
    addRow(list, 'a', 'one')
    addRow(list, 'b', 'two')
    addRow(list, 'c', 'three')
    await flush()
    expect(rec.rowCount).toBe(3)
  })
})
