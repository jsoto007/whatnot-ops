import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'
import { cell, receive } from './stock'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Orders ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`orders-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

function row(seq: number, username: string, body: string) {
  return {
    id: (seq + 0x2000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username,
    body,
    rawText: `${username} ${body}`.trim(),
    capturedAt: new Date(Date.parse(SHOW) + seq * 1000).toISOString(),
    seq,
  }
}

test('wins are paid orders at once; a declined card comes off the queue; pick → pack → ship', async ({ page, request }) => {
  await signup(page)
  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await receive(page, 'RED', 5)
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('5')

  const key = await connectLaptop(page, request)
  const res = await request.post('/api/ingest', {
    data: {
      version: 1,
      show: { id: SHOW, startedAt: SHOW },
      rows: [row(1, '', 'lani808 won Red bundle'), row(2, '', 'lani808 won Red bundle x2'), row(3, '', 'kat.c won Red bundle')],
    },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(res.ok()).toBe(true)

  // Whatnot charges at the win: both orders are ready to pick at once, nothing awaits payment.
  await page.getByRole('link', { name: /^Orders/ }).click()
  await expect(page.getByRole('tab', { name: /Awaiting payment/ })).toHaveCount(0)
  await expect(page.getByTestId('order-lani808')).toContainText('3 · 1× RED, 2× RED')
  await expect(page.getByTestId('order-lani808')).toContainText('ready')
  await expect(page.getByTestId('order-kat.c')).toContainText('1 · 1× RED')
  // Paid at the win: all four units left the shelf (5 − 3 − 1 = 1).
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('1')
  await expect(cell(page, 'RED', 'held')).toHaveText('0')

  // Then the platform says kat.c's card was declined: the sale closes and the unit comes back.
  const declined = await request.post('/api/ingest', {
    data: { version: 1, show: { id: SHOW, startedAt: SHOW }, rows: [row(4, '', 'payment failed for kat.c')] },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(declined.ok()).toBe(true)
  await page.reload()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('2')
  await page.getByRole('link', { name: /^Orders/ }).click()
  await expect(page.getByTestId('order-kat.c')).toHaveCount(0) // not ready to pick any more
  await page.getByRole('tab', { name: /Payment declined/ }).click()
  await expect(page.getByTestId('declined-kat.c')).toContainText('payment declined')

  // The payment file only confirms: lani808's two lots are already paid, kat.c's stays declined.
  await page.getByRole('link', { name: /^Shows/ }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  await page.getByRole('tab', { name: 'Payments' }).click()
  await page.getByTestId('reconcile-file').setInputFiles({
    name: 'orders.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'Order ID,Buyer,Product,Quantity,Sold Price,Status\nW1,lani808,Red bundle,1,25.00,Paid\nW2,lani808,Red bundle,2,50.00,Paid\nW3,kat.c,Red bundle,1,25.00,Payment failed\n',
    ),
  })
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('reconcile-summary')).toContainText('3 unchanged')
  await page.getByTestId('apply-reconcile').click()
  await page.getByRole('tab', { name: 'Sales' }).click()
  await expect(page.getByTestId('sales').locator('tr', { hasText: 'kat.c' })).toContainText('payment declined')
  await expect(page.getByTestId('sales').locator('tr', { hasText: 'lani808' }).first()).toContainText('paid')
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('2')

  // Queue: lani808's order is verified; pick list shows 3 × RED; pick → pack → ship.
  await page.getByRole('link', { name: /^Orders/ }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('ready')
  await expect(page.getByTestId('order-kat.c')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Show' }).selectOption({ index: 1 })
  await page.getByRole('link', { name: 'Pick list for this show' }).click()
  await expect(page.getByTestId('pick-list')).toContainText('RED')
  await expect(page.getByTestId('pick-list').locator('td').nth(2)).toHaveText('3')
  await page.goBack()
  await page.getByTestId('order-lani808').getByRole('button', { name: 'Start picking' }).click()
  await page.getByRole('tab', { name: 'In progress' }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('picking')
  await page.getByTestId('order-lani808').getByRole('link').click()
  // the slip reads like the shop's paper sheet: header, a tick box per unit, footer lines
  await expect(page.getByTestId('slip-title')).toHaveText(/Username: lani808 – 3 Items/)
  await expect(page.getByTestId('packing-slip')).toContainText('Product checklist')
  await expect(page.getByTestId('packing-slip').getByTestId('tick')).toHaveCount(3) // one box per unit
  await expect(page.getByTestId('slip-foot')).toContainText('Packed by:')
  await page.getByTestId('advance').click() // packed
  await expect(page.getByTestId('slip-foot')).toContainText('Packed by: Olive Owner')
  await page.getByLabel('Tracking number').fill('1Z999')
  await page.getByTestId('advance').click() // shipped
  await expect(page.locator('.status.shipped').first()).toBeVisible()
  await expect(page.getByTestId('packing-slip')).toContainText('shipped')
})
