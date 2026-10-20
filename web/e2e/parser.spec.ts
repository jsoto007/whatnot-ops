import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Parse ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`parse-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app\/shows$/)
}

function row(seq: number, username: string, body: string) {
  return {
    id: (seq + 0x1000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username,
    body,
    rawText: `${username} ${body}`.trim(),
    capturedAt: new Date(Date.parse(SHOW) + seq * 1000).toISOString(),
    seq,
  }
}

test('captured chat becomes sales, reserves stock, and the review queue resolves the rest', async ({ page, request }) => {
  await signup(page)

  // Stock: 3 red bundles. Nothing for "mystery box".
  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByRole('button', { name: 'Add product' }).click()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await page.getByLabel('Adjust RED').fill('+3')
  await page.getByTestId('level-RED').getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(2)).toHaveText('3')

  // A device key, then "the extension" posts a batch through the real ingest API.
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Laptop name').fill('Laptop')
  await page.getByRole('button', { name: 'Create key' }).click()
  const key = (await page.getByTestId('new-key').locator('code').textContent())!.trim()
  const batch = {
    version: 1,
    show: { id: SHOW, startedAt: SHOW },
    rows: [
      row(1, '', 'Auction #1: Red bundle'),
      row(2, 'lani808', 'mine'),
      row(3, '', '🎉 lani808 won the auction for $25.00'),
      row(4, '', 'pokemom_22 won Red bundle x2'),
      row(5, '', 'cardguy bought mystery box for $10'),
      row(6, 'cardguy', 'thanks!'),
      row(7, '', 'Cancelled for pokemom_22'),
      row(8, 'lani808', 'please cancel mine, card declined'), // not a host line: only the AI audit acts on it
    ],
  }
  const res = await request.post('/api/ingest', { data: batch, headers: { Authorization: 'Bearer ' + key } })
  expect(res.ok()).toBe(true)

  // The show appears; parse it (JOBS_INLINE already parsed on ingest, "Parse now" is idempotent).
  await page.getByRole('link', { name: 'Shows' }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  // Jobs run inline in e2e, so ingest already parsed the show and the fake AI audit already ran:
  // lani808's "card declined" line was a confident, reversible finding and the AI applied it.
  await page.getByTestId('parse-now').click()
  await expect(page.getByTestId('tile-parsed')).toContainText('8')
  await expect(page.getByTestId('tile-sales')).toContainText('1') // cardguy; lani808 and pokemom are in the cancelled tile
  await expect(page.getByTestId('tile-cancelled')).toContainText('2')
  await expect(page.getByTestId('tile-review')).toContainText('1')

  const sales = page.getByTestId('sales')
  await expect(sales.locator('tr', { hasText: 'lani808' })).toContainText('payment failed')
  await expect(sales.locator('tr', { hasText: 'pokemom_22' })).toContainText('cancelled')
  await expect(sales.locator('tr', { hasText: 'cardguy' })).toContainText('unmatched')

  // Running the audit again proposes nothing new.
  await page.getByTestId('ai-audit').click()
  await expect(page.getByTestId('tile-cancelled')).toContainText('2')
  await expect(page.getByTestId('tile-review')).toContainText('1')

  // Stock: nothing reserved (lani808 released by the AI, pokemom by the rules); 3 available.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(3)).toHaveText('0')
  await expect(page.getByTestId('level-RED').locator('td').nth(4)).toContainText('3')

  // The AI's action is in the activity log and the Settings page shows the provider.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('ai-status')).toContainText('Provider fake')
  await expect(page.locator('.table.small')).toContainText('ai supervisor')

  // Review: teach the app that "mystery box" is a red bundle.
  await page.getByRole('link', { name: 'Review' }).click()
  await expect(page.locator('.review')).toHaveCount(1)
  const select = page.getByLabel(/SKU for review/)
  const redValue = await select.locator('option', { hasText: 'RED' }).getAttribute('value')
  await select.selectOption(redValue!)
  await page.getByRole('button', { name: 'Use this SKU' }).click()
  await expect(page.getByText('Nothing waiting')).toBeVisible()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(3)).toHaveText('1')
  await page.getByRole('link', { name: 'Products' }).click()
  await expect(page.locator('.chip', { hasText: 'mystery box' })).toBeVisible()
})
