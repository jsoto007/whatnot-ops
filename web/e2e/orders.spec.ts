import { expect, test, type Page } from '@playwright/test'

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
  await expect(page).toHaveURL(/\/app\/shows$/)
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

test('sales become orders; the payment file verifies them; the queue drives pick → pack → ship', async ({ page, request }) => {
  await signup(page)
  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await page.getByLabel('Adjust RED').fill('+5')
  await page.getByTestId('level-RED').getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(2)).toHaveText('5')

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Laptop name').fill('Laptop')
  await page.getByRole('button', { name: 'Create key' }).click()
  const key = (await page.getByTestId('new-key').locator('code').textContent())!.trim()
  const res = await request.post('/api/ingest', {
    data: {
      version: 1,
      show: { id: SHOW, startedAt: SHOW },
      rows: [row(1, '', 'lani808 won Red bundle'), row(2, '', 'lani808 won Red bundle x2'), row(3, '', 'kat.c won Red bundle')],
    },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(res.ok()).toBe(true)

  // Two buyers → two open orders, nothing to fulfil yet.
  await page.getByRole('link', { name: 'Orders' }).click()
  await expect(page.getByText('Nothing to fulfil')).toBeVisible()
  await page.getByRole('tab', { name: 'Awaiting payment' }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('3')
  await expect(page.getByTestId('order-kat.c')).toContainText('1')

  // Reconcile: lani808 paid, kat.c refunded.
  await page.getByRole('link', { name: 'Shows' }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  await page.getByRole('tab', { name: 'Payments' }).click()
  await page.getByTestId('reconcile-file').setInputFiles({
    name: 'orders.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'Order ID,Buyer,Product,Quantity,Sold Price,Status\nW1,lani808,Red bundle,1,25.00,Paid\nW2,lani808,Red bundle,2,50.00,Paid\nW3,kat.c,Red bundle,1,25.00,Refunded\n',
    ),
  })
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('reconcile-summary')).toContainText('2 mark paid')
  await expect(page.getByTestId('reconcile-summary')).toContainText('1 cancel')
  await page.getByTestId('apply-reconcile').click()
  await page.getByRole('tab', { name: 'Sales' }).click()
  await expect(page.getByTestId('sales').locator('tr', { hasText: 'kat.c' })).toContainText('cancelled')
  await expect(page.getByTestId('sales').locator('tr', { hasText: 'lani808' }).first()).toContainText('paid')

  // Stock left the shelf for the paid ones: 5 − 3 on hand, 0 reserved.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(2)).toHaveText('2')
  await expect(page.getByTestId('level-RED').locator('td').nth(3)).toHaveText('0')

  // Queue: lani808's order is verified; pick list shows 3 × RED; pick → pack → ship.
  await page.getByRole('link', { name: 'Orders' }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('paid')
  await expect(page.getByTestId('order-kat.c')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Show' }).selectOption({ index: 1 })
  await page.getByRole('link', { name: 'Pick list' }).click()
  await expect(page.getByTestId('pick-list')).toContainText('RED')
  await expect(page.getByTestId('pick-list').locator('td').nth(2)).toHaveText('3')
  await page.goBack()
  await page.getByTestId('order-lani808').getByRole('button', { name: 'Start picking' }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('picking')
  await page.getByTestId('order-lani808').getByRole('link').click()
  await expect(page.getByTestId('packing-slip')).toContainText('all paid')
  await page.getByTestId('advance').click() // packed
  await page.getByLabel('Tracking number').fill('1Z999')
  await page.getByTestId('advance').click() // shipped
  await expect(page.locator('.status.shipped').first()).toBeVisible()
  await expect(page.getByTestId('packing-slip')).toContainText('fulfilled')
})
