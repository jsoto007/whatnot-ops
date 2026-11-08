import { expect, test } from './fixtures'
import { API_PORT, E2E_DEVICE_KEY } from './playwright.config'

const API = `http://127.0.0.1:${API_PORT}`
const panel = (sel: string) => `#cs-host ${sel}`

/** Connect the laptop to the e2e web app with its device key, through the extension's Settings page. */
async function connect(context: import('@playwright/test').BrowserContext, extensionId: string): Promise<void> {
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
}

test("after a show, the panel reads every page of Seller Hub's Shipments and sends it to the web app", async ({ page, context, extensionId }) => {
  await connect(context, extensionId)

  await page.goto('/dashboard/shipments/')
  await expect(page.locator(panel('#ship'))).toBeVisible()
  await expect(page.locator(panel('#shipNext'))).toBeHidden()
  await page.locator(panel('#shipSend')).click()
  // Two pages (2 + 1 rows), each shipment opened for its lots; the e2e shop has no show on
  // the date in Whatnot's show filter, so nothing is filed under a show.
  await expect(page.locator(panel('#shipSt'))).toHaveText('Sent 3 shipments (4 lots) · 3 matched no show in Sales Ops.', { timeout: 30_000 })
  await expect(page.locator('#showing')).toHaveText('Showing 3-3 of 3') // it pressed Next
  await expect(page.locator('#rows tr')).toHaveCount(1) // and closed what it opened
  await expect(page.locator(panel('#shipNote'))).toContainText('Order slips')

  // With "All Shows" picked there is no one show to file the read under: it refuses.
  await page.locator('#showFilter').evaluate((b) => (b.firstChild!.textContent = 'All Shows & Marketplace'))
  await page.locator(panel('#shipSend')).click()
  await expect(page.locator(panel('#shipSt'))).toContainText("Pick this show in Whatnot's show filter first", { timeout: 20_000 })
})

test('the send also works when Seller Hub was reached without a page load (in-page navigation)', async ({ page, context, extensionId }) => {
  await connect(context, extensionId)
  // Seller Hub is a single page: the host opens it elsewhere and clicks into Shipments. Chrome
  // then still reports the first URL as the message sender's; the background used to refuse.
  await page.goto('/chat.html')
  await expect(page.locator(panel('#ship'))).toBeHidden()
  const html = await (await page.request.get('/dashboard/shipments/index.html')).text()
  await page.evaluate((html) => {
    history.pushState({}, '', '/dashboard/shipments/?filters=e2e')
    const doc = new DOMParser().parseFromString(html, 'text/html')
    const host = document.getElementById('cs-host')
    document.body.innerHTML = doc.body.innerHTML
    if (host) document.body.appendChild(host)
    const script = document.createElement('script') // innerHTML never runs the fixture's script
    script.textContent = doc.querySelector('script')?.textContent ?? ''
    document.body.appendChild(script)
  }, html)
  await expect(page.locator(panel('#ship'))).toBeVisible({ timeout: 15_000 })
  await page.locator(panel('#shipSend')).click()
  await expect(page.locator(panel('#shipSt'))).toHaveText('Sent 3 shipments (4 lots) · 3 matched no show in Sales Ops.', { timeout: 30_000 })
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
