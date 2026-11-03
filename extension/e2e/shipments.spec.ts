import { expect, test } from './fixtures'
import { API_PORT, E2E_DEVICE_KEY } from './playwright.config'

const API = `http://127.0.0.1:${API_PORT}`
const panel = (sel: string) => `#cs-host ${sel}`

test("after a show, the panel reads every page of Seller Hub's Shipments and sends it to the web app", async ({ page, context, extensionId }) => {
  const options = await context.newPage()
  await options.goto(`chrome-extension://${extensionId}/options.html`)
  await options.locator('#url').fill(API + '/')
  await options.getByText('Use a device key instead').click()
  await options.locator('#key').fill(E2E_DEVICE_KEY)
  await options.locator('#test-key').click()
  await expect(options.locator('#msg')).toHaveText('Connected as "e2e device" (E2E Shop).')
  await options.locator('#save').click()
  await expect(options.locator('#msg')).toContainText('Saved.')
  await options.close()

  await page.goto('/dashboard/shipments/')
  await expect(page.locator(panel('#ship'))).toBeVisible()
  await expect(page.locator(panel('#shipNext'))).toBeHidden()
  await page.locator(panel('#shipSend')).click()
  // Two pages (2 + 1 rows); no show on those dates in the e2e shop, so all three are unmatched.
  await expect(page.locator(panel('#shipSt'))).toHaveText('Sent 3 shipments · 3 had no show on their date.', { timeout: 20_000 })
  await expect(page.locator('#showing')).toHaveText('Showing 3-3 of 3') // it pressed Next
  await expect(page.locator(panel('#shipNote'))).toContainText('Order slips')
})

test('Stop points the host at the Shipments page; recording does not', async ({ page, context }) => {
  const next = page.locator(panel('#shipNext'))
  await page.goto('/chat.html')
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(2).click()
  await expect(page.locator(panel('#st'))).toHaveText('Watching chat.')
  await expect(next).toBeHidden()
  await page.locator(panel('#stop')).click()
  await expect(next).toBeVisible()
  const [tab] = await Promise.all([context.waitForEvent('page'), page.locator(panel('#shipOpen')).click()])
  expect(tab.url()).toBe('https://www.whatnot.com/dashboard/shipments')
  await tab.close()
})
