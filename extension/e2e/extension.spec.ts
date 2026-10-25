import { expect, test } from './fixtures'

declare global {
  interface Window {
    __chat: {
      append(user: string, text: string): number
      appendMany(count: number, prefix?: string): number
      rerender(k?: number): void
      rerenderAll(): void
      swapClasses(next?: string): void
      replaceContainer(): void
      count(): number
    }
    __player: {
      idle(title: string, price?: string): void
      bidding(name: string, price: string, title: string): void
      won(name: string, price: string, title: string): void
    }
  }
}

const panel = (sel: string) => `#cs-host ${sel}`

test.setTimeout(120_000)

test('captures a show end to end: pick, 500 rows with re-renders, class swap, container swap, resume', async ({
  page,
  context,
  extensionId,
}) => {
  await page.goto('/chat.html')
  await expect(page.locator(panel('#st'))).toHaveText('Ready. Open the show, then press Start.')

  // Pick a row by clicking on the message words.
  await page.locator(panel('#start')).click()
  await expect(page.locator(panel('#st'))).toContainText('click one chat message')
  await page.locator('.msg-row .msg-text').nth(3).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await expect(page.locator(panel('#cnt'))).toHaveText('10') // the seed rows

  // 500 new rows, with Whatnot-style re-renders of the last 5 every 50.
  await page.evaluate(() => {
    for (let i = 0; i < 500; i++) {
      window.__chat.append('buyer' + (i + 1), 'I will take ' + ((i % 3) + 1))
      if (i % 50 === 49) window.__chat.rerender(5)
    }
  })
  await expect(page.locator(panel('#cnt'))).toHaveText('510')
  await expect(page.locator(panel('#sync'))).toContainText('saved 510')

  // A deploy renames every row class; rows keep coming with the new class.
  await page.evaluate(() => window.__chat.swapClasses('msg-row-v2'))
  for (let i = 0; i < 8; i++) {
    await page.evaluate((i) => window.__chat.append('late' + i, 'after the swap ' + i), i)
    await page.waitForTimeout(150)
  }
  await expect(page.locator(panel('#st'))).toContainText('found the messages again', { timeout: 20_000 })
  await expect(page.locator(panel('#cnt'))).toHaveText('518')
  await page.evaluate(() => window.__chat.append('late9', 'after the heal'))
  await expect(page.locator(panel('#cnt'))).toHaveText('519')

  // A full re-render of the list long after the rows were seen must not re-count them,
  // while a new message in the same render is kept.
  await page.waitForTimeout(5500) // past the 5-second text window on purpose
  await page.evaluate(() => {
    window.__chat.rerenderAll()
    window.__chat.append('withRender', 'new in the same render')
  })
  await page.waitForTimeout(1000)
  await expect(page.locator(panel('#cnt'))).toHaveText('520')

  // The whole scroll container is replaced (reconnect); existing text must not be re-counted.
  await page.evaluate(() => window.__chat.replaceContainer())
  await page.evaluate(() => window.__chat.append('fresh1', 'after container swap'))
  await expect(page.locator(panel('#cnt'))).toHaveText('521', { timeout: 20_000 })
  await expect(page.locator(panel('#sync'))).toContainText('saved 521')

  // Chat Search sees the same show, live, with the username split applied.
  const search = await context.newPage()
  await search.goto(`chrome-extension://${extensionId}/search.html`)
  await expect(search.locator('#show option').first()).toContainText('521 msgs')
  await expect(search.locator('#summary')).toContainText('521')
  await search.locator('#q').fill('buyer77')
  await expect(search.locator('#summary')).toContainText('1 message matching')
  await expect(search.locator('.line.m')).toContainText('buyer77 I will take')
  // Off Whatnot the toolbar icon lands here, so pairing must be one click away.
  const [settings] = await Promise.all([context.waitForEvent('page'), search.locator('#settings').click()])
  await expect(settings).toHaveURL(`chrome-extension://${extensionId}/options.html`)
  await expect(settings.locator('#pair')).toBeVisible()
  await settings.close()
  await search.close()

  // Reload mid-show: the panel offers to resume and does not double count what is on screen.
  await page.reload()
  await expect(page.locator(panel('#st'))).toContainText('Found 521 saved messages')
  await page.locator(panel('#resume')).click()
  await page.locator('.msg-row .msg-text').nth(2).click() // the reloaded fixture starts over with the seed rows
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await expect(page.locator(panel('#cnt'))).toHaveText('521')
  await page.evaluate(() => window.__chat.append('resumed1', 'first after resume'))
  await expect(page.locator(panel('#cnt'))).toHaveText('522')
  await expect(page.locator(panel('#sync'))).toContainText('saved 522')
})

test('Resume / Start fresh only after a reload, and Start fresh keeps the earlier show', async ({ page, context, extensionId }) => {
  const resumeRow = page.locator(panel('#resumeRow'))
  await page.goto('/chat.html')
  await expect(resumeRow).toBeHidden()
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(2).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await expect(resumeRow).toBeHidden()
  await page.evaluate(() => ['a', 'b', 'c'].forEach((u) => window.__chat.append('first_' + u, 'line')))
  await expect(page.locator(panel('#sync'))).toContainText('saved 13')
  await expect(resumeRow).toBeHidden()

  await page.reload()
  await expect(page.locator(panel('#st'))).toContainText('Found 13 saved messages')
  await expect(resumeRow).toBeVisible()
  await page.locator(panel('#fresh')).click()
  await expect(page.locator(panel('#st'))).toHaveText('Ready. Press Start.')
  await expect(page.locator(panel('#cnt'))).toHaveText('0')
  await expect(resumeRow).toBeHidden()

  // A new show: the reloaded fixture's seed lines plus two more, all saved.
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(2).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await page.evaluate(() => ['x', 'y'].forEach((u) => window.__chat.append('second_' + u, 'line')))
  await expect(page.locator(panel('#cnt'))).toHaveText('12')
  await expect(page.locator(panel('#sync'))).toContainText('saved 12')

  const search = await context.newPage()
  await search.goto(`chrome-extension://${extensionId}/search.html`)
  await expect(search.locator('#show option')).toHaveCount(2)
  await expect(search.locator('#show')).toContainText('13 msgs')
  await expect(search.locator('#show')).toContainText('12 msgs')
})

test('wins come from the video player: one row each, shown in the panel and in Chat Search', async ({
  page,
  context,
  extensionId,
}) => {
  await page.goto('/chat.html')
  // A "won!" already up when recording starts ended before it: never recorded.
  await page.evaluate(() => window.__player.won('earlybird', '$5', 'Lot #751'))
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(2).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await page.waitForTimeout(1500)
  await expect(page.locator(panel('#wins'))).toBeHidden()
  await page.evaluate(() => window.__player.bidding('ladyyy03', '$190', 'Lot #752'))

  await page.evaluate(() => window.__player.won('chonwhite828', '$195', 'Lot #752'))
  await expect(page.locator(panel('#wins'))).toHaveText('wins 1 · last: chonwhite828 won the auction for Lot #752 for $195.00')
  await expect(page.locator(panel('#cnt'))).toHaveText('11') // 10 seed lines + the win
  // "won!" stays up and re-renders for seconds: still one win.
  await page.waitForTimeout(1200)
  await page.evaluate(() => window.__player.won('chonwhite828', '$195', 'Lot #752'))
  await page.waitForTimeout(1200)
  await expect(page.locator(panel('#cnt'))).toHaveText('11')

  // The next lot, same buyer: a new win.
  await page.evaluate(() => window.__player.idle('Lot #753'))
  await page.evaluate(() => window.__player.bidding('trickmon2', '$40', 'Lot #753'))
  await page.waitForTimeout(700)
  await page.evaluate(() => window.__player.won('chonwhite828', '$45', 'Lot #753'))
  await expect(page.locator(panel('#wins'))).toHaveText('wins 2 · last: chonwhite828 won the auction for Lot #753 for $45.00')
  await expect(page.locator(panel('#sync'))).toContainText('saved 12')

  const search = await context.newPage()
  await search.goto(`chrome-extension://${extensionId}/search.html`)
  await search.locator('#q').fill('won the auction')
  await expect(search.locator('#summary')).toContainText('2 messages matching')

  // A reload while "won!" is still on screen does not record it again.
  await page.reload()
  await page.evaluate(() => window.__player.won('chonwhite828', '$45', 'Lot #753'))
  await page.locator(panel('#resume')).click()
  await page.locator('.msg-row .msg-text').nth(2).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await expect(page.locator(panel('#wins'))).toHaveText('wins 2 · last: chonwhite828 won the auction for Lot #753 for $45.00')
  await page.waitForTimeout(1200)
  await expect(page.locator(panel('#cnt'))).toHaveText('12')
})

test('says so when the click was not on a list of messages', async ({ page }) => {
  await page.goto('/chat.html')
  await page.locator(panel('#start')).click()
  await page.locator('.video').click()
  await expect(page.locator(panel('#st'))).toContainText("Couldn't recognise that as a chat message")
  await expect(page.locator(panel('#start'))).toBeEnabled()
})

test('the panel moves by its header, resizes from its corner, stays on screen and remembers', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 })
  await page.goto('/chat.html')
  const host = page.locator('#cs-host')
  const box = page.locator(panel('#p'))
  const hd = page.locator(panel('#hd'))
  await expect(page.locator(panel('#st'))).toHaveText('Ready. Open the show, then press Start.')
  const start = (await box.boundingBox())!
  expect(start.x + start.width).toBeCloseTo(1200 - 16, 0) // bottom-right corner by default
  expect(start.y + start.height).toBeCloseTo(800 - 16, 0)
  expect(start.height).toBeLessThanOrEqual(800 - 32) // never taller than the window

  // Drag by the title to the top-left.
  const h = (await hd.boundingBox())!
  await page.mouse.move(h.x + 60, h.y + h.height / 2)
  await page.mouse.down()
  await page.mouse.move(200, 120, { steps: 8 })
  await page.mouse.up()
  const moved = (await box.boundingBox())!
  expect(moved.x).toBeLessThan(200)
  expect(moved.y).toBeLessThan(120)

  // Resize from the bottom-right grip.
  const g = (await page.locator(panel('#grip')).boundingBox())!
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2)
  await page.mouse.down()
  await page.mouse.move(g.x + 140, g.y - 120, { steps: 8 })
  await page.mouse.up()
  const sized = (await box.boundingBox())!
  expect(sized.width).toBeGreaterThan(moved.width + 100)
  expect(sized.height).toBeLessThan(moved.height - 80)
  // The body scrolls inside the smaller panel; the controls are still there.
  await page.locator(panel('#find')).scrollIntoViewIfNeeded()
  await expect(page.locator(panel('#find'))).toBeVisible()

  // Dragged far off screen, it stays inside the window.
  const h2 = (await hd.boundingBox())!
  await page.mouse.move(h2.x + 60, h2.y + h2.height / 2)
  await page.mouse.down()
  await page.mouse.move(-400, -300, { steps: 6 })
  await page.mouse.up()
  const clamped = (await box.boundingBox())!
  expect(clamped.x).toBeGreaterThanOrEqual(8)
  expect(clamped.y).toBeGreaterThanOrEqual(8)

  // A reload puts it back where it was, at the same size.
  await page.reload()
  await expect(page.locator(panel('#st'))).toBeVisible()
  await expect.poll(async () => (await box.boundingBox())!.x).toBeCloseTo(clamped.x, 0)
  expect((await box.boundingBox())!.width).toBeCloseTo(sized.width, 0)

  // Double-clicking the title sends it back to the corner at its normal size, for good.
  await page.locator(panel('.ttl')).dblclick()
  await expect.poll(async () => host.evaluate((el) => (el as HTMLElement).style.right)).toBe('16px')
  await page.reload()
  await expect(page.locator(panel('#st'))).toBeVisible()
  const back = (await box.boundingBox())!
  expect(back.x + back.width).toBeCloseTo(1200 - 16, 0)
  expect(back.width).toBeCloseTo(start.width, 0)
})
