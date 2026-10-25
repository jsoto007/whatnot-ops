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
  // The seed leaves 4: not low yet, so neither the list nor an alert shows.
  await page.waitForTimeout(1500)
  await expect(page.locator(panel('#low'))).toBeHidden()
  await expect(page.locator(panel('#alert'))).toBeHidden()

  // Buyers unique per run: the same buyer/item/price as an earlier run's show on the test DB
  // would rightly be held in Review as "the same win in another recording of this show".
  const run = Date.now().toString(36)
  const steps: [string, string][] = [
    [`buyer_a_${run}`, '3 left: E2E Mug (E2E-MUG)'],
    [`buyer_b_${run}`, '2 left: E2E Mug (E2E-MUG)'],
    [`buyer_c_${run}`, '1 left: E2E Mug (E2E-MUG)'],
    [`buyer_d_${run}`, 'sold out: E2E Mug (E2E-MUG)'],
  ]
  for (const [i, [buyer, alert]] of steps.entries()) {
    await page.evaluate((b) => {
      window.__player.idle('E2E Mug')
      window.__player.bidding('someone', '$9', 'E2E Mug')
      setTimeout(() => window.__player.won(b, '$10', 'E2E Mug'), 700)
    }, buyer)
    await expect(page.locator(panel('#alert'))).toHaveText(alert, STEP)
    await expect(page.locator(panel('#low'))).toContainText('E2E Mug (E2E-MUG)')
    if (i === 2) await expect(page.locator(panel('#alert'))).toHaveClass(/urgent/)
    await page.locator(panel('#alert')).click() // dismissed; the list stays
    await expect(page.locator(panel('#alert'))).toBeHidden()
  }
  await expect(page.locator(panel('#low'))).toContainText('sold out')

  // Still visible with the panel minimised; Stop clears it.
  await page.locator(panel('#min')).click()
  await expect(page.locator(panel('#low'))).toBeVisible()
  await page.locator(panel('#min')).click()
  await page.locator(panel('#stop')).click()
  await expect(page.locator(panel('#low'))).toBeHidden()
})
