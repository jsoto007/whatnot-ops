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
    ],
  }
  const res = await request.post('/api/ingest', { data: batch, headers: { Authorization: 'Bearer ' + key } })
  expect(res.ok()).toBe(true)

  // The show appears; parse it (JOBS_INLINE already parsed on ingest, "Parse now" is idempotent).
  await page.getByRole('link', { name: 'Shows' }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  await page.getByTestId('parse-now').click()
  await expect(page.getByTestId('tile-parsed')).toContainText('7')
  await expect(page.getByTestId('tile-sales')).toContainText('2') // lani808 + cardguy; pokemom's is in the cancelled tile
  await expect(page.getByTestId('tile-cancelled')).toContainText('1')
  await expect(page.getByTestId('tile-review')).toContainText('1')

  const sales = page.getByTestId('sales')
  await expect(sales).toContainText('lani808')
  await expect(sales.locator('tr', { hasText: 'lani808' })).toContainText('reserved')
  await expect(sales.locator('tr', { hasText: 'pokemom_22' })).toContainText('cancelled')
  await expect(sales.locator('tr', { hasText: 'cardguy' })).toContainText('unmatched')

  // Stock: lani808 holds 1 of 3; pokemom's 2 were released.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(3)).toHaveText('1')
  await expect(page.getByTestId('level-RED').locator('td').nth(4)).toContainText('2')

  // Review: teach the app that "mystery box" is a red bundle.
  await page.getByRole('link', { name: 'Review' }).click()
  await expect(page.locator('.review')).toHaveCount(1)
  const select = page.getByLabel(/SKU for review/)
  const redValue = await select.locator('option', { hasText: 'RED' }).getAttribute('value')
  await select.selectOption(redValue!)
  await page.getByRole('button', { name: 'Use this SKU' }).click()
  await expect(page.getByText('Nothing waiting')).toBeVisible()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(3)).toHaveText('2')
  await page.getByRole('link', { name: 'Products' }).click()
  await expect(page.locator('.chip', { hasText: 'mystery box' })).toBeVisible()
})
