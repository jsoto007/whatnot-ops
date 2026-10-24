// The whole laptop process, as a shop would do it: sign up in the portal, download the extension
// from Settings → Laptops, load that zip in Chrome, reach Settings from Chat Search, pair with a
// code, record a "Whatnot" show, and see the uploaded chat parsed into sales in the portal.
import { chromium, expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PORT } from './playwright.config'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = readFileSync(path.join(HERE, '..', 'fixture', 'chat.html'), 'utf8')
const BASE = `http://127.0.0.1:${PORT}`
const run = Date.now().toString(36)
const panel = (sel: string) => `#cs-host ${sel}`

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Flow ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`flow-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('download → pair → record a show → parsed sales in the portal', async ({ page }) => {
  // ---- Owner, in the portal ----
  await signup(page)
  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByRole('button', { name: 'Add product' }).click()
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Your Whatnot username (the host)').fill('shop_host')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByLabel('Your Whatnot username (the host)')).toHaveValue('shop_host')

  // Step 1 of Laptops: the download button serves a zip with this portal's address baked in.
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('download-extension').click()])
  const dir = mkdtempSync(path.join(tmpdir(), 'chat-scraper-'))
  const zip = path.join(dir, dl.suggestedFilename())
  await dl.saveAs(zip)
  const ext = path.join(dir, 'unzipped')
  execFileSync('unzip', ['-q', zip, '-d', ext])
  expect(JSON.parse(readFileSync(path.join(ext, 'defaults.json'), 'utf8'))).toEqual({ serverUrl: BASE })

  // Step 2: a one-time code.
  await page.getByRole('button', { name: 'Connect a laptop' }).click()
  const sheet = page.getByRole('dialog', { name: 'Connect a laptop' })
  await sheet.getByLabel('Which laptop is this?').fill('Front laptop')
  await sheet.getByRole('button', { name: 'Get a code' }).click()
  const code = (await sheet.getByTestId('pairing-code').textContent())!.trim()
  expect(code).toMatch(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)

  // ---- The laptop: Chrome with the downloaded zip loaded unpacked ----
  const laptop = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
  })
  try {
    let [sw] = laptop.serviceWorkers()
    if (!sw) sw = await laptop.waitForEvent('serviceworker')
    const id = sw.url().split('/')[2]!
    // whatnot.com is the fixture chat page, so the real build's content script (whatnot.com only) runs on it.
    await laptop.route('https://www.whatnot.com/**', (r) => r.fulfill({ contentType: 'text/html', body: FIXTURE }))

    // Off Whatnot the toolbar icon opens Chat Search; Settings is one click from there.
    const search = await laptop.newPage()
    await search.goto(`chrome-extension://${id}/search.html`)
    const [options] = await Promise.all([laptop.waitForEvent('page'), search.locator('#settings').click()])
    await expect(options).toHaveURL(`chrome-extension://${id}/options.html`)
    await expect(options.locator('#url')).toHaveValue(BASE) // from defaults.json, nothing to type
    await options.locator('#code').fill(code)
    await options.locator('#pair').click()
    await expect(options.locator('#msg')).toHaveText(`Connected as "Front laptop" (Flow ${run}).`)
    await expect(sheet.getByText('Front laptop is connected')).toBeVisible({ timeout: 10_000 })
    await sheet.getByRole('button', { name: 'Done' }).click()

    // Record the show. Nothing to resume on a fresh install, so Resume / Start fresh stay hidden:
    // a mid-show "Start fresh" used to drop every line after it.
    const show = await laptop.newPage()
    await show.goto('https://www.whatnot.com/live/full-flow-test')
    const resumeRow = show.locator(panel('#resumeRow'))
    await expect(show.locator(panel('#st'))).toHaveText('Ready. Open the show, then press Start.')
    await expect(resumeRow).toBeHidden()
    await show.locator(panel('#start')).click()
    await show.locator('.msg-row .msg-text').nth(2).click()
    await expect(show.locator(panel('#st'))).toHaveText('Watching chat.')
    await expect(resumeRow).toBeHidden()
    await expect(show.locator(panel('#cnt'))).toHaveText('10') // the fixture's seed lines
    await show.evaluate(() => {
      const c = window.__chat
      c.append('', 'Auction #1: Red bundle')
      c.append('lani808', 'mine')
      c.append('', '🎉 lani808 won the auction for $25.00')
      c.append('', 'cardguy bought mystery box for $10')
      c.append('shop_host', 'thanks everyone, next one coming up')
      c.append('scammer99', 'won the auction!') // a viewer's line: never a sale without a person
    })
    await expect(show.locator(panel('#cnt'))).toHaveText('16')
    await expect(show.locator(panel('#sync'))).toHaveText('saved 16 · uploaded', { timeout: 20_000 })
    // Whatnot shows the next win only in the video player, never in the chat.
    await show.evaluate(() => window.__player.bidding('ladyyy03', '$190', 'Red bundle #752'))
    await show.waitForTimeout(700)
    await show.evaluate(() => window.__player.won('chonwhite828', '$195', 'Red bundle #752'))
    await expect(show.locator(panel('#wins'))).toHaveText('wins 1 · last: chonwhite828 won the auction for Red bundle #752 for $195.00')
    await expect(show.locator(panel('#cnt'))).toHaveText('17')
    // The host says it in chat too: the same win, so it must not become a second sale.
    await show.evaluate(() => window.__chat.append('shop_host', 'sold to chonwhite828 - red bundle'))
    await expect(show.locator(panel('#cnt'))).toHaveText('18')
    await expect(show.locator(panel('#sync'))).toHaveText('saved 18 · uploaded', { timeout: 20_000 })
    await expect(resumeRow).toBeHidden()
    await show.locator(panel('#stop')).click()

    await options.reload()
    await expect(options.locator('#stats')).toContainText('Uploaded 18 · pending 0', { timeout: 10_000 })
  } finally {
    await laptop.close()
  }

  // ---- Back in the portal: the laptop is live and the show is parsed ----
  await page.reload()
  await expect(page.getByTestId('devices').locator('tr', { hasText: 'Front laptop' })).toContainText(/Uploading now|Seen/)
  await page.getByRole('link', { name: /^Shows/ }).click()
  await expect(page.getByRole('row', { name: /Sat|Sun|Mon|Tue|Wed|Thu|Fri/ }).first()).toContainText('18')
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  await expect(page.getByTestId('tile-items')).toContainText('3')
  const sales = page.getByTestId('sales')
  // The player win: buyer, item without the lot number, the price paid, stock held.
  const win = sales.locator('tr', { hasText: 'chonwhite828' })
  await expect(win).toContainText('Red bundle')
  await expect(win).toContainText('$195.00')
  await expect(win).toContainText('reserved')
  await expect(sales.locator('tr', { hasText: 'lani808' })).toContainText('Red bundle')
  await expect(sales.locator('tr', { hasText: 'lani808' })).toContainText('$25.00')
  await expect(sales.locator('tr', { hasText: 'cardguy' })).toContainText('$10.00')
  await expect(sales.locator('tr', { hasText: 'scammer99' })).toHaveCount(0)

  // Review: every card shows the lines around it, so pick the card by its heading.
  await page.getByRole('link', { name: /^Review/ }).click()
  const unsure = page.locator('.review', { has: page.getByRole('heading', { name: /^Not sure/ }) })
  await expect(unsure).toHaveCount(1)
  await expect(unsure).toContainText('"scammer99 won the auction!"')
  await expect(page.locator('.review', { has: page.getByRole('heading', { name: /^Item not recognised/ }) })).toContainText(
    'cardguy bought "mystery box"',
  )
  const twice = page.locator('.review', { has: page.getByRole('heading', { name: /^Same win twice\?/ }) })
  await expect(twice).toHaveCount(1)
  await expect(twice).toContainText("this show's wins come from the video player")
})
