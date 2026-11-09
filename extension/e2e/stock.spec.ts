import { expect, test } from './fixtures'
import { API_PORT, E2E_DEVICE_KEY } from './playwright.config'

const API = `http://127.0.0.1:${API_PORT}`
const panel = (sel: string) => `#cs-host ${sel}`

// Upload (~3.5 s), parse, then the next stock poll (8 s): up to ~12 s per step.
const STEP = { timeout: 25_000 }
test.setTimeout(150_000)

test('player wins count E2E Mug down and the panel calls out 3, 2, 1 left and sold out', async ({
  page,
  context,
  extensionId,
}) => {
  const options = await context.newPage()
  await options.goto(`chrome-extension://${extensionId}/options.html`)
  await options.locator('#url').fill(API)
  await options.getByText('Use a device key instead').click()
  await options.locator('#key').fill(E2E_DEVICE_KEY)
  await options.locator('#save').click()
  await expect(options.locator('#connected-card')).toBeVisible()
  await options.close()

  await page.goto('/chat.html')
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(1).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  // The seed leaves 4: not low yet, so neither the running-low row nor a notice shows. E2E Spare is
  // down to 1, but this show has not sold it, so the panel does not mention it either.
  await page.waitForTimeout(1500)
  await expect(page.locator(panel('#lowbtn'))).toBeHidden()
  await expect(page.locator(panel('#toasts .toast'))).toHaveCount(0)

  // Buyers unique per run: the same buyer/item/price as an earlier run's show on the test DB
  // would rightly be held in Review as "the same win in another recording of this show".
  const run = Date.now().toString(36)
  const steps: [string, string][] = [
    [`buyer_a_${run}`, '3 left'],
    [`buyer_b_${run}`, '2 left'],
    [`buyer_c_${run}`, '1 left'],
    [`buyer_d_${run}`, 'Sold out'],
  ]
  const low = page.locator(panel('#low'))
  for (const [i, [buyer, title]] of steps.entries()) {
    await page.evaluate((b) => {
      window.__player.idle('E2E Mug')
      window.__player.bidding('someone', '$9', 'E2E Mug')
      setTimeout(() => window.__player.won(b, '$10', 'E2E Mug'), 700)
    }, buyer)
    // Each drop is a notice beside the panel (the win is one too, so take the stock one):
    // amber, red from 1 left; a click takes it away.
    const notice = page.locator(panel('#toasts .toast[data-group="stock"]')).last()
    await expect(notice).toContainText(title, STEP)
    await expect(notice).toContainText('E2E Mug · E2E-MUG')
    await expect(notice).toHaveClass(i >= 2 ? /urgent/ : /low/)
    await notice.click()
    await expect(notice).toBeHidden()
    // The running-low row counts; it opens the list, which then stays open.
    await expect(page.locator(panel('#lown'))).toHaveText('1 running low')
    if (i === 0) await page.locator(panel('#lowbtn')).click()
    await expect(low).toBeVisible()
    await expect(low).toContainText('E2E Mug')
    await expect(low).toContainText('E2E-MUG')
    await expect(low).not.toContainText('E2E Spare')
  }
  await expect(low).toContainText('sold out')

  // Minimised, only the pill shows; the list is back when the panel is. Stop clears it.
  await page.locator(panel('#min')).click()
  await expect(page.locator(panel('#pill'))).toBeVisible()
  await expect(low).toBeHidden()
  await page.locator(panel('#pill')).click()
  await expect(low).toBeVisible()
  await page.locator(panel('#stop')).click()
  await expect(low).toBeHidden()
  await expect(page.locator(panel('#lowbtn'))).toBeHidden()
})
