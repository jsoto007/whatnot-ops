import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'
import { cell, receive } from './stock'

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
  await expect(page).toHaveURL(/\/app$/)
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
  await receive(page, 'RED', 3)
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('3')

  // A device key, then "the extension" posts a batch through the real ingest API.
  const key = await connectLaptop(page, request)
  // The host's own lines are the ones the shop (and the AI) may act on.
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Your Whatnot username (the host)').fill('shop_host')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByLabel('Your Whatnot username (the host)')).toHaveValue('shop_host')
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
      row(8, 'shop_host', 'lani808 card declined, relisting'), // host line the rules miss: the AI audit acts on it
      row(9, 'viewer_vic', 'won the auction!'), // the platform's winner line, split so the winner is the sender: a person decides
    ],
  }
  const res = await request.post('/api/ingest', { data: batch, headers: { Authorization: 'Bearer ' + key } })
  expect(res.ok()).toBe(true)

  // The show appears; parse it (JOBS_INLINE already parsed on ingest, "Parse now" is idempotent).
  await page.getByRole('link', { name: /^Shows/ }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  // Jobs run inline in e2e, so ingest already parsed the show and the fake AI audit already ran:
  // the host's "card declined" line was a confident, reversible finding the AI could tie to the
  // host, so it applied it (a buyer or viewer line alone would only become a proposal).
  await page.getByTestId('parse-now').click()
  await expect(page.getByTestId('tile-items')).toContainText('1') // cardguy's; lani808 and pokemom are cancelled/failed
  await expect(page.getByTestId('tile-items')).toContainText('2 cancelled or failed')
  await expect(page.getByTestId('status-bar')).toContainText('1 cancelled')
  await expect(page.getByTestId('status-bar')).toContainText('1 payment failed')
  await expect(page.getByTestId('next-steps')).toContainText('2 lines need a decision')

  const sales = page.getByTestId('sales')
  await expect(sales.locator('tr', { hasText: 'lani808' })).toContainText('payment failed')
  await expect(sales.locator('tr', { hasText: 'pokemom_22' })).toContainText('cancelled')
  await expect(sales.locator('tr', { hasText: 'cardguy' })).toContainText('unmatched')

  // Running the audit again proposes nothing new.
  await page.getByTestId('ai-audit').click()
  await expect(page.getByTestId('tile-items')).toContainText('2 cancelled or failed')
  await expect(page.getByTestId('next-steps')).toContainText('2 lines need a decision')

  // Stock: nothing reserved (lani808 released by the AI, pokemom by the rules); 3 available.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'held')).toHaveText('0')
  await expect(cell(page, 'RED', 'available')).toContainText('3')

  // The AI's action is in the activity log and the Settings page shows the provider.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('ai-status')).toContainText('Provider fake')
  await expect(page.locator('.table.small')).toContainText('ai supervisor')

  // Review: teach the app that "mystery box" is a red bundle.
  await page.getByRole('link', { name: /^Review/ }).click()
  await expect(page.locator('.review')).toHaveCount(2)
  const select = page.getByLabel(/SKU for review/)
  const redValue = await select.locator('option', { hasText: 'RED' }).getAttribute('value')
  await select.selectOption(redValue!)
  await page.getByRole('button', { name: 'Use this SKU' }).click()
  await expect(page.locator('.review')).toHaveCount(1)
  // The split winner line: the rules would not trust it, a person vouches for it and it becomes
  // a real sale (item from the listing), stock is held, and it leaves the queue for good.
  const split = page.locator('.review', { hasText: 'viewer_vic' })
  await expect(split).toContainText('Not sure')
  await split.getByTestId('confirm-sale').click()
  await expect(page.getByText('Nothing waiting', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'held')).toHaveText('2')
  await page.getByRole('link', { name: 'Products' }).click()
  await expect(page.locator('.chip', { hasText: 'mystery box' })).toBeVisible()

  // Overselling: 3 on the shelf, 2 held, kat.c wins 4 more. The sale is reserved anyway and
  // the shortfall says how many to buy, instead of a "not enough stock" review.
  const more = await request.post('/api/ingest', {
    data: { ...batch, rows: [row(10, '', 'kat.c won Red bundle x4')] },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(more.ok()).toBe(true)
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'held')).toHaveText('6')
  await expect(cell(page, 'RED', 'available')).toContainText('-3')
  await expect(cell(page, 'RED', 'available')).toContainText('buy 3')
  await expect(page.getByTestId('to-buy')).toHaveText('1 oversold, buy 3 more')
  await page.getByRole('link', { name: /^Review/ }).click()
  await expect(page.getByText('Nothing waiting', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Dashboard' }).click()
  await expect(page.locator('.attn-row', { hasText: 'Buy 3 more Red bundle' })).toBeVisible()

  // "Receive what's owed" pre-fills the 3; receiving 5 instead leaves 2 to sell.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await page.getByRole('button', { name: 'Receive what’s owed' }).click()
  await expect(page.getByLabel('Quantity, line 1')).toHaveValue('3')
  await page.getByLabel('Quantity, line 1').fill('5')
  await page.getByTestId('receive-submit').click()
  await expect(cell(page, 'RED', 'available')).toHaveText('2')
  await expect(page.getByTestId('to-buy')).toHaveCount(0)
})
