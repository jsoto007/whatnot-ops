import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'
import { cell, receive } from './stock'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

async function signup(page: Page, tag = '') {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Orders ${run}${tag}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`orders-${run}${tag}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

function row(seq: number, username: string, body: string, source?: 'player') {
  return {
    id: (seq + 0x2000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username,
    body,
    rawText: `${username} ${body}`.trim(),
    capturedAt: new Date(Date.parse(SHOW) + seq * 1000).toISOString(),
    seq,
    ...(source ? { source } : {}),
  }
}

/** A win read from the video player: the lot's full title, Whatnot's lot number included. */
const win = (seq: number, buyer: string, lot: number, price = '$7.00') => row(seq, '', `${buyer} won the auction for $1 BEAUTY PULLS #${lot} for ${price}`, 'player')

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
  await expect(page.getByTestId('order-lani808')).toContainText('3 × Red bundle')
  await expect(page.getByTestId('order-lani808')).toContainText('Ready to pick')
  await expect(page.getByTestId('order-kat.c')).toContainText('1 × Red bundle')
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
  await expect(page.getByTestId('order-lani808')).toContainText('Ready to pick')
  await expect(page.getByTestId('order-kat.c')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Show' }).selectOption({ index: 1 })
  await page.getByRole('link', { name: 'Pick list' }).click()
  await expect(page.getByTestId('pick-list')).toContainText('RED')
  await expect(page.getByTestId('pick-list').locator('td').nth(2)).toHaveText('3')
  await page.goBack()
  await page.getByTestId('order-lani808').getByRole('button', { name: 'Start picking' }).click()
  await page.getByRole('tab', { name: 'In progress' }).click()
  await expect(page.getByTestId('order-lani808')).toContainText('Picking')
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

test('a lot nobody recognised is named from its row: that lot alone, or every lot with the title', async ({ page, request }) => {
  await signup(page, '-name')
  await page.getByRole('link', { name: 'Products' }).click()
  // Neither its name nor its code shares a word with the lot title ("$1" counts as a word),
  // so neither the rules nor the (fake) AI can match it on their own: a person must.
  await page.getByLabel('Product name').fill('Mystery Pull Bag')
  await page.getByLabel('SKU code (optional)').fill('MPB')
  await page.getByLabel('Price (USD, optional)').fill('1')
  await page.getByRole('button', { name: 'Add product' }).click()
  await receive(page, 'MPB', 10)
  const key = await connectLaptop(page, request)
  // Three winners of "$1 BEAUTY PULLS": lots #520, #521 and #522. None matches a product by name.
  const res = await request.post('/api/ingest', {
    data: { version: 1, show: { id: SHOW, startedAt: SHOW }, rows: [win(1, 'moonlove3', 520), win(2, 'taravat08', 521), win(3, 'amyshopsalot', 522, '$9.00')] },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(res.ok()).toBe(true)

  await page.getByRole('link', { name: /^Orders/ }).click()
  await page.getByRole('tab', { name: /Needs review/ }).click()
  const moon = page.getByTestId('order-moonlove3')
  await expect(moon).toContainText('Needs review')
  await expect(moon).toContainText('1 × “$1 BEAUTY PULLS”')
  await expect(moon).toContainText('#520')
  await expect(page.getByTestId('stuck-banner')).toContainText('3 orders are waiting on one item')

  // From the row: the pick is for moonlove3's lot #520 and nothing else.
  await moon.getByRole('button', { name: 'Name the product' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Which product is “$1 BEAUTY PULLS #520”?')
  await expect(dialog).toContainText('moonlove3 · lot #520')
  // "… #520" names one lot of one show, so it is never learned: the box is not offered.
  await expect(dialog.getByRole('checkbox', { name: /Remember this name/ })).toHaveCount(0)
  await expect(dialog.getByTestId('apply-similar')).not.toBeChecked()
  await dialog.getByRole('combobox').fill('mystery')
  await dialog.getByRole('option', { name: /Mystery Pull Bag/ }).click()
  await expect(dialog.getByTestId('stock-note')).toContainText('10 available · 1 unit will be sold')
  await dialog.getByTestId('use-product').click()
  await expect(page.getByRole('status')).toContainText("Mystery Pull Bag set for moonlove3's lot #520.")
  await expect(page.getByTestId('order-moonlove3')).toHaveCount(0) // ready now, so off this tab
  await expect(page.getByTestId('order-taravat08')).toContainText('Needs review')
  await expect(page.getByTestId('order-amyshopsalot')).toContainText('Needs review')

  // The banner: name it once for the two lots that are left.
  await expect(page.getByTestId('stuck-banner')).toContainText('2 orders are waiting on one item')
  await page.getByTestId('name-it-once').click()
  await expect(dialog).toContainText('Which product is “$1 BEAUTY PULLS #521”?')
  await expect(dialog.getByTestId('apply-similar')).toBeChecked()
  await dialog.getByRole('combobox').fill('mpb')
  await dialog.getByRole('option', { name: /Mystery Pull Bag/ }).click()
  await expect(dialog.getByTestId('use-product')).toHaveText('Use it for 2 lots')
  await dialog.getByTestId('use-product').click()
  await expect(page.getByRole('status')).toContainText("set for taravat08's lot #521 and 1 other lot.")
  await expect(page.getByTestId('stuck-banner')).toHaveCount(0)
  await page.getByRole('tab', { name: /Ready to pick/ }).click()
  await expect(page.getByTestId('order-moonlove3')).toContainText('Ready to pick')
  await expect(page.getByTestId('order-taravat08')).toContainText('Ready to pick')
  await expect(page.getByTestId('order-amyshopsalot')).toContainText('$9.00')
  // Paid at the win: three units left the shelf.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'MPB', 'on-hand')).toHaveText('7')
})
