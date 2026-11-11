// Updating Chat Scraper without removing it: a laptop on an older build sees the update in the
// web app, the new download is copied over the folder Chrome loads it from, and the web app's
// Reload button brings the new version up. Same extension, so it stays paired and keeps its data.
import { chromium, expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { PORT } from './playwright.config'

const BASE = `http://127.0.0.1:${PORT}`
const run = Date.now().toString(36)
const EMAIL = `update-${run}@shop.test`
const PASSWORD = 'correct-horse-battery'
const OLD = '1.4.9'

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Update ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password (10+ characters)').fill(PASSWORD)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('an older laptop updates in place from the web app and stays connected', async ({ page }) => {
  // ---- Owner: download the extension the portal serves ----
  await signup(page)
  await page.getByRole('link', { name: 'Settings' }).click()
  const latest = (await page.getByTestId('download-extension').textContent())!.replace(/.*v/, '').trim()
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('download-extension').click()])
  const dir = mkdtempSync(path.join(tmpdir(), 'chat-scraper-update-'))
  const zip = path.join(dir, dl.suggestedFilename())
  await dl.saveAs(zip)
  // The laptop's folder holds an older build: same code, a lower version number.
  const ext = path.join(dir, 'Chat Scraper')
  execFileSync('unzip', ['-q', zip, '-d', ext])
  const manifestPath = path.join(ext, 'manifest.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  expect(manifest.externally_connectable).toEqual({ matches: ['http://127.0.0.1/*'] })
  writeFileSync(manifestPath, JSON.stringify({ ...manifest, version: OLD }, null, 2))

  await page.getByRole('button', { name: 'Connect a laptop' }).click()
  const sheet = page.getByRole('dialog', { name: 'Connect a laptop' })
  await sheet.getByLabel('Which laptop is this?').fill('Front laptop')
  await sheet.getByRole('button', { name: 'Get a code' }).click()
  const code = (await sheet.getByTestId('pairing-code').textContent())!.trim()

  const laptop = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
  })
  try {
    let [sw] = laptop.serviceWorkers()
    if (!sw) sw = await laptop.waitForEvent('serviceworker')
    const id = sw.url().split('/')[2]!
    // "Load unpacked" needs Developer mode, so a real laptop has it on. Chrome turns off a
    // reloaded unpacked extension without it.
    const extensions = await laptop.newPage()
    await extensions.goto('chrome://extensions')
    await extensions.evaluate(
      () =>
        new Promise((r) =>
          (globalThis as unknown as { chrome: any }).chrome.developerPrivate.updateProfileConfiguration({ inDeveloperMode: true }, r), // eslint-disable-line @typescript-eslint/no-explicit-any
        ),
    )

    // Pair, then the extension's Settings already says a newer version is waiting.
    const options = await laptop.newPage()
    await options.goto(`chrome-extension://${id}/options.html`)
    await expect(options.locator('#version')).toHaveText(`v${OLD}`)
    await options.locator('#code').fill(code)
    await options.locator('#pair').click()
    await expect(options.locator('#msg')).toHaveText(`Connected as "Front laptop" (Update ${run}).`)
    await sheet.getByRole('button', { name: 'Done' }).click()
    await options.locator('#test').click()
    await expect(options.locator('#update-card')).toBeVisible()
    await expect(options.locator('#latest')).toHaveText(latest)

    // The owner sees which laptop is behind.
    await page.reload()
    const row = page.getByTestId('devices').locator('tr', { hasText: 'Front laptop' })
    await expect(row.getByTestId('device-version')).toHaveText(`${OLD}Update`)

    // ---- On the laptop, in the web app ----
    const portal = await laptop.newPage()
    await portal.goto(`${BASE}/login`)
    await portal.getByLabel('Email').fill(EMAIL)
    await portal.getByLabel('Password').fill(PASSWORD)
    await portal.getByRole('button', { name: 'Sign in' }).click()
    const banner = portal.getByTestId('update-banner')
    await expect(banner).toHaveText(new RegExp(`Chat Scraper ${latest} is ready\\. This laptop has ${OLD}\\.`))
    await banner.getByRole('button', { name: 'Update' }).click()
    const update = portal.getByRole('dialog', { name: 'Update Chat Scraper' })
    await expect(update.getByTestId('update-files')).toHaveText(`Waiting for the new files. The folder still has ${OLD}.`)
    await expect(update.getByTestId('update-reload')).toBeDisabled()

    // Copy the new download over the folder, as the sheet says. Chrome reads the files from disk.
    execFileSync('unzip', ['-q', '-o', zip, '-d', ext])
    await expect(update.getByTestId('update-files')).toHaveText(`✓ New files found (${latest})`, { timeout: 10_000 })
    await update.getByTestId('update-reload').click()
    await expect(update.getByTestId('update-done')).toContainText(`Chat Scraper ${latest} is running`, { timeout: 25_000 })
    await update.getByRole('button', { name: 'Done' }).click()
    await expect(banner).toHaveCount(0)

    // Same extension: still connected, nothing to update.
    let [sw2] = laptop.serviceWorkers().filter((w) => w.url().includes(id))
    if (!sw2) sw2 = await laptop.waitForEvent('serviceworker')
    expect(sw2.url()).toContain(id)
    // Chrome closes an extension's own tabs when it reloads; open Settings again.
    const after = await laptop.newPage()
    await after.goto(`chrome-extension://${id}/options.html`)
    await expect(after.locator('#version')).toHaveText(`v${latest}`)
    await expect(after.locator('#who')).toHaveText(`Connected as "Front laptop" (Update ${run})`)
    await expect(after.locator('#update-card')).toBeHidden()

    await page.reload()
    await expect(row.getByTestId('device-version')).toHaveText(latest)
  } finally {
    await laptop.close()
  }
})
