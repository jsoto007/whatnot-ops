import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'
import { cell, receive } from './stock'

/**
 * Whatnot's Seller API, end to end in the browser, against the in-memory Whatnot the test
 * server runs with (WHATNOT_API=fake). It follows Whatnot's published API description; no
 * part of this has run against the live Seller API.
 */
const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date(Date.now() - 90 * 60_000).toISOString()

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Whatnot ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`whatnot-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

const win = (seq: number, buyer: string, title: string, price: string) => {
  const body = `${buyer} won the auction for ${title} for ${price}`
  return {
    id: (seq + 0x7000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username: '',
    body,
    rawText: body,
    capturedAt: new Date(Date.parse(SHOW) + seq * 60_000).toISOString(),
    seq,
    source: 'player',
  }
}

test("connect the shop, compare Whatnot's orders with the show's sales, apply them", async ({ page, request }) => {
  await signup(page)
  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await receive(page, 'RED', 5)

  // A show the laptop recorded: two wins read from the video player.
  const key = await connectLaptop(page, request)
  const sent = await request.post('/api/ingest', {
    data: { version: 1, show: { id: SHOW, startedAt: SHOW }, rows: [win(1, 'lani808', 'Red bundle', '$25.00'), win(2, 'kat.c', 'Red bundle', '$25.00')] },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(sent.ok()).toBe(true)

  // What Whatnot has for that show: lani808 paid, kat.c's card failed, and a win the laptop missed.
  const seeded = await request.post('/api/whatnot/fake', {
    data: {
      seller: { id: String(Date.now()), username: `shop_${run}` },
      shows: [{ title: `Friday night ${run}`, status: 'ended', startedMinutesAgo: 85, endedMinutesAgo: 1 }],
      orders: [
        { buyer: 'lani808', items: [{ title: 'Red bundle', priceCents: 2500 }], minutesAgo: 80 },
        { buyer: 'kat.c', items: [{ title: 'Red bundle', priceCents: 2500 }], status: 'failed', minutesAgo: 70 },
        { buyer: 'newbie', items: [{ title: 'CHOOSE ANY PRODUCT YOU WANT #73', priceCents: 1700 }], minutesAgo: 60 },
      ],
    },
  })
  expect(seeded.status()).toBe(201)

  // Settings → Whatnot: the owner connects; the page says who the shop is on Whatnot.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('whatnot-state')).toHaveText('Not connected.')
  await page.getByTestId('whatnot-connect').click()
  await expect(page.getByTestId('whatnot-state')).toContainText(`Connected as @shop_${run}`)
  await expect(page.getByTestId('whatnot-mode').getByRole('radio', { name: /Compare only/ })).toBeChecked()

  // The show's Payments tab: matched to the Whatnot show on its own; read its orders.
  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await page.getByRole('row').nth(1).getByRole('link').click()
  await expect(page.getByTestId('show-title')).toHaveText(`Friday night ${run}`)
  await page.getByRole('tab', { name: 'Payments' }).click()
  await expect(page.getByTestId('whatnot-orders-state')).toContainText(`Friday night ${run} · Ended`)
  await page.getByTestId('whatnot-refresh').click()
  const rows = page.getByTestId('whatnot-rows')
  await expect(rows.getByRole('row').filter({ hasText: 'lani808' })).toContainText('nothing to change')
  await expect(rows.getByRole('row').filter({ hasText: 'kat.c' })).toContainText('payment failed')
  await expect(rows.getByRole('row').filter({ hasText: 'kat.c' })).toContainText('mark the payment declined')
  await expect(rows.getByRole('row').filter({ hasText: 'newbie' })).toContainText('record the sale')
  // Comparing changed nothing: both units are still off the shelf.
  await page.getByTestId('whatnot-apply').click()
  await expect(page.getByTestId('whatnot-applied')).toContainText('1 mark the payment declined')
  await expect(page.getByTestId('whatnot-applied')).toContainText('1 sale recorded')
  await expect(page.getByTestId('whatnot-apply')).toHaveText('Nothing to apply')

  // kat.c's unit is back on the shelf; newbie has an order waiting for its product to be named.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('4')
  await page.getByRole('link', { name: /^Orders/ }).click()
  await page.getByRole('tab', { name: /Needs review/ }).click()
  await expect(page.getByTestId('order-newbie')).toContainText('CHOOSE ANY PRODUCT YOU WANT')
  await page.getByRole('tab', { name: /Payment declined/ }).click()
  await expect(page.getByTestId('order-kat.c')).toBeVisible()

  // The activity log says what happened, in words, with nothing internal.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByText("Applied Whatnot's orders to a show: 1 payment declined, 1 sales recorded")).toBeVisible()
  await expect(page.getByText(`Connected the shop to Whatnot as @shop_${run}`)).toBeVisible()
})
