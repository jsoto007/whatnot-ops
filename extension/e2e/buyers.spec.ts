import { expect, test } from './fixtures'
import { API_PORT, E2E_DEVICE_KEY } from './playwright.config'

const API = `http://127.0.0.1:${API_PORT}`
const panel = (sel: string) => `#cs-host ${sel}`

test.setTimeout(90_000)

test('a win says who the buyer is: a regular the team flagged, then someone new', async ({ page, context, extensionId }) => {
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
  await expect(page.locator(panel('#who'))).toBeHidden()

  const win = (buyer: string, lot: string) =>
    page.evaluate(
      ([b, l]) => {
        window.__player.idle(l!)
        window.__player.bidding('someone', '$9', l!)
        setTimeout(() => window.__player.won(b!, '$12', l!), 700)
      },
      [buyer, lot],
    )

  // The seed's regular bought 2 items in a show last January and is flagged VIP. (Counts
  // grow if this ran on an earlier day against the same test database: any number passes.)
  await win('e2e_regular', 'Lot #501')
  const back = /^VIP · back again · \d+ items over \d+ shows?$/
  const notice = page.locator(panel('#toasts .toast.win')).last()
  await expect(notice).toContainText('e2e_regular won · $12.00')
  await expect(notice.locator('.more')).toHaveText(back)
  await expect(page.locator(panel('#who'))).toHaveText(/^e2e_regular: VIP · back again · \d+ items over \d+ shows?$/)

  // Someone the shop has never sold to. The line is about the latest win only.
  const stranger = `newcomer_${Date.now().toString(36)}`
  await win(stranger, 'Lot #502')
  await expect(page.locator(panel('#who'))).toHaveText(`${stranger}: New buyer`)
  await expect(page.locator(panel('#toasts .toast.win')).last().locator('.more')).toHaveText('New buyer')

  // Nothing about a buyer stays up once the recording stops.
  await page.locator(panel('#stop')).click()
  await expect(page.locator(panel('#who'))).toBeHidden()
})
