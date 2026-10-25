import { expect, test } from './fixtures'
import { API_PORT, E2E_DEVICE_KEY, E2E_PAIRING_CODE } from './playwright.config'

const API = `http://127.0.0.1:${API_PORT}`
const panel = (sel: string) => `#cs-host ${sel}`

test('uploads captured rows to the API, recovers from a wrong key, and never double-counts', async ({
  page,
  context,
  extensionId,
  request,
}) => {
  // Configure the server through the options page, with a wrong key first.
  const options = await context.newPage()
  await options.goto(`chrome-extension://${extensionId}/options.html`)
  await options.locator('#url').fill(API + '/')
  await options.getByText('Use a device key instead').click()
  await options.locator('#key').fill('wn_wrong')
  await options.locator('#test-key').click()
  await expect(options.locator('#msg')).toHaveText('The server rejected this key.')
  await options.locator('#save').click()

  // Capture rows: they are saved locally and the panel says the upload failed.
  await page.goto('/chat.html')
  await page.locator(panel('#start')).click()
  await page.locator('.msg-row .msg-text').nth(1).click()
  await expect(page.locator(panel('#cnt'))).toHaveText('10')
  await page.evaluate(() => {
    for (let i = 0; i < 30; i++) window.__chat.append('buyer' + i, 'I will take ' + i)
  })
  await expect(page.locator(panel('#cnt'))).toHaveText('40')
  await expect(page.locator(panel('#sync'))).toContainText('rejected the API key', { timeout: 15_000 })
  await expect(page.locator(panel('#sync'))).toContainText('40 to upload')

  // Fix the key: the background notices the change and drains the outbox.
  await options.locator('#key').fill(E2E_DEVICE_KEY)
  await options.locator('#test-key').click()
  await expect(options.locator('#msg')).toHaveText('Connected as "e2e device" (E2E Shop).')
  await options.locator('#save').click()
  await expect(page.locator(panel('#sync'))).toHaveText('saved 40 · uploaded', { timeout: 15_000 })
  await expect(options.locator('#stats')).toContainText('Uploaded 40 · pending 0', { timeout: 10_000 })

  // The API has exactly those rows, with the username split applied.
  const search = await context.newPage()
  await search.goto(`chrome-extension://${extensionId}/search.html`)
  const showKey = await search.locator('#show option').first().getAttribute('value')
  expect(showKey).toBeTruthy()
  const auth = { Authorization: 'Bearer ' + E2E_DEVICE_KEY }
  const status = await request.get(`${API}/api/ingest/shows/${encodeURIComponent(showKey!)}`, { headers: auth })
  expect(status.ok()).toBe(true)
  expect((await status.json()).messageCount).toBe(40)

  // More rows while connected upload on their own; a reload carries on and re-sends nothing.
  await page.evaluate(() => window.__chat.append('late', 'one more'))
  await expect(page.locator(panel('#sync'))).toHaveText('saved 41 · uploaded', { timeout: 15_000 })
  await page.reload()
  await expect(page.locator(panel('#st'))).toContainText('Watching chat.')
  await page.evaluate(() => window.__chat.append('after', 'resume'))
  await expect(page.locator(panel('#cnt'))).toHaveText('42')
  await expect(page.locator(panel('#sync'))).toHaveText('saved 42 · uploaded', { timeout: 15_000 })
  const again = await request.get(`${API}/api/ingest/shows/${encodeURIComponent(showKey!)}`, { headers: auth })
  expect((await again.json()).messageCount).toBe(42)
})

test('a pairing code from the web app connects the laptop in one step', async ({ context, extensionId }) => {
  const options = await context.newPage()
  await options.goto(`chrome-extension://${extensionId}/options.html`)
  await options.locator('#url').fill(API)
  await expect(options.locator('#pair-card')).toBeVisible()
  await options.locator('#code').fill('nope-nope')
  await options.locator('#pair').click()
  await expect(options.locator('#msg')).toContainText('That code did not work')
  await options.locator('#code').fill(E2E_PAIRING_CODE.toLowerCase())
  await options.locator('#code').press('Enter')
  await expect(options.locator('#msg')).toHaveText('Connected as "Paired laptop" (E2E Shop).')
  await expect(options.locator('#connected-card')).toBeVisible()
  await expect(options.locator('#pair-card')).toBeHidden()
  // The key was saved: a reload shows the laptop as connected and the server agrees.
  await options.reload()
  await expect(options.locator('#who')).toHaveText('Connected as "Paired laptop" (E2E Shop)')
  await options.locator('#test').click()
  await expect(options.locator('#msg')).toHaveText('Connected as "Paired laptop" (E2E Shop).')
  // The code was single-use.
  options.on('dialog', (d) => void d.accept())
  await options.locator('#disconnect').click()
  await expect(options.locator('#pair-card')).toBeVisible()
  await options.locator('#code').fill(E2E_PAIRING_CODE)
  await options.locator('#pair').click()
  await expect(options.locator('#msg')).toContainText('wrong, used or expired')
})
