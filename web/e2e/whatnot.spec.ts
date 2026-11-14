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

async function signup(page: Page, tag = '') {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Whatnot ${run}${tag}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`whatnot-${run}${tag}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

/** E2E_SHOTS=<folder> saves a picture of each new screen (for a write-up); off by default. */
const shot = async (page: Page, name: string) => {
  if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/${name}.png`, fullPage: true })
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
        { buyer: 'splitsy', items: [{ title: 'Red bundle', priceCents: 2500 }], minutesAgo: 50 },
        { buyer: 'splitsy', items: [{ title: 'Red bundle', priceCents: 2600 }], minutesAgo: 40 },
      ],
      // One parcel per buyer is the rule; splitsy's two wins ended up in two open parcels.
      shipments: [
        { recipient: 'lani808', orders: [0] },
        { recipient: 'newbie', orders: [2] },
        { recipient: 'splitsy', orders: [3] },
        { recipient: 'splitsy', orders: [4] },
      ],
    },
  })
  expect(seeded.status()).toBe(201)
  const fake = (await seeded.json()) as { shows: string[]; shipments: string[] }
  // How Whatnot says the show did (it works these out some time after the show).
  const usd = (amount: string) => ({ amount, currency: 'USD' })
  await request.post('/api/whatnot/fake', {
    data: { results: [{ showId: fake.shows[0], data: { sales: usd('93.00'), estimated_earnings: usd('80.10'), orders_count: 4, average_order_value: usd('23.25'), buyers_count: 3, first_time_buyers_count: 1, returning_buyers_count: 2, distinct_viewers_count: 412, max_concurrent_viewers: 57, stream_minutes: 84 } }] },
  })

  // Settings → Whatnot: the owner connects; the page says who the shop is on Whatnot.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('whatnot-state')).toHaveText('Not connected.')
  await page.getByTestId('whatnot-connect').click()
  await expect(page.getByTestId('whatnot-state')).toContainText(`Connected as @shop_${run}`)
  await expect(page.getByTestId('whatnot-mode').getByRole('radio', { name: /Compare only/ })).toBeChecked()
  await shot(page, '1-settings-connected')

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
  await shot(page, '2-payments-compare')
  // Comparing changed nothing: both units are still off the shelf.
  await page.getByTestId('whatnot-apply').click()
  await expect(page.getByTestId('whatnot-applied')).toHaveText('1 payment declined · 3 newly recorded')
  await expect(page.getByTestId('whatnot-apply')).toHaveText('Nothing to apply')

  // Shipping: one package and one label per buyer.
  await page.getByTestId('show-shipping-link').click()
  await page.getByTestId('shipping-refresh').click()
  await expect(page.getByTestId('ship-lani808').getByTestId('ship-state')).toHaveText('Ready for its label')
  await expect(page.getByTestId('ship-splitsy').getByTestId('ship-state')).toHaveText('Two open packages: combine them in Seller Hub first')
  await expect(page.getByTestId('ship-splitsy').getByTestId('ship-package')).toHaveCount(2)
  // Two labels to make (lani808, newbie); the page asks first and says what it closes.
  await expect(page.getByTestId('shipping-make-labels')).toHaveText('Make 2 labels')
  await shot(page, '3-shipping-before-labels')
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain("closes its buyer's bundle")
    await dialog.accept()
  })
  await page.getByTestId('shipping-make-labels').click()
  await expect(page.getByTestId('shipping-note')).toContainText('Asked Whatnot for 2 labels')
  // Whatnot was asked for exactly those two parcels, never either of splitsy's.
  const made = await request.post('/api/whatnot/fake', { data: { finishLabels: true } })
  expect(((await made.json()) as { labelsRequested: string[] }).labelsRequested.sort()).toEqual([fake.shipments[0], fake.shipments[1]].sort())
  await page.getByTestId('shipping-refresh').click()
  await expect(page.getByTestId('ship-lani808').getByTestId('ship-state')).toHaveText('Label ready')
  await shot(page, '4-shipping-labels-ready')
  // "Print labels": both labels as one PDF in a new tab; each is marked printed, and nothing is left to print.
  await expect(page.getByTestId('shipping-print-labels')).toHaveText('Print 2 labels')
  const tab = page.waitForEvent('popup')
  await page.getByTestId('shipping-print-labels').click()
  await expect(page.getByTestId('shipping-note')).toHaveText('Opened 2 labels to print.')
  await (await tab).close()
  // The tab was closed before anything was printed: the page still has a way back to that PDF.
  await expect(page.getByTestId('shipping-open-pdf')).toHaveText('Open the last PDF again')
  await expect(page.getByTestId('shipping-print-state')).toContainText('Every label of this show has been printed (2)')
  await expect(page.getByTestId('shipping-print-labels')).toHaveCount(0)
  await expect(page.getByTestId('ship-lani808').getByTestId('ship-printed')).toContainText('Label printed')
  await shot(page, '4b-shipping-labels-printed')
  // One specific label can still be printed again from its row.
  const print = page.getByTestId('ship-lani808').getByTestId('ship-print')
  await expect(print).toHaveText('Print label and slip again')
  const pdf = await page.request.get((await print.getAttribute('href'))!)
  expect(pdf.status()).toBe(200)
  expect(pdf.headers()['content-type']).toContain('application/pdf')
  expect((await pdf.body()).subarray(0, 4).toString()).toBe('%PDF')
  // The carrier takes lani808's parcel: the order can be marked shipped without clicking through picking.
  await request.post('/api/whatnot/fake', { data: { setShipments: [{ id: fake.shipments[0], status: 'dropped_off' }] } })
  await page.getByTestId('shipping-refresh').click()
  await page.getByTestId('shipping-mark-shipped').click()
  await expect(page.getByTestId('shipping-note')).toHaveText('1 order marked shipped.')

  // kat.c's unit is back on the shelf (lani808 and splitsy's two are gone: 5 − 3 = 2).
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('2')
  await page.getByRole('link', { name: /^Orders/ }).click()
  await page.getByRole('tab', { name: /Shipped/ }).click()
  await expect(page.getByTestId('order-lani808')).toBeVisible()
  // Shipped: where the parcel is, as Whatnot last said. The carrier has it.
  await expect(page.getByTestId('parcel-lani808')).toContainText('On its way')
  await expect(page.getByTestId('parcel-lani808')).toContainText('9400111899223344556677')
  await expect(page.getByTestId('parcel-filters').getByRole('button', { name: /On its way/ })).toContainText('1')
  await shot(page, '4c-orders-shipped-parcel')
  await page.getByRole('link', { name: /^Orders/ }).click()
  await page.getByRole('tab', { name: /Needs review/ }).click()
  await expect(page.getByTestId('order-newbie')).toContainText('CHOOSE ANY PRODUCT YOU WANT')
  await page.getByRole('tab', { name: /Payment declined/ }).click()
  await expect(page.getByTestId('order-kat.c')).toBeVisible()

  // The show's Analytics tab: Whatnot's own results for it.
  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await page.getByRole('row').nth(1).getByRole('link').click()
  await page.getByRole('tab', { name: 'Analytics' }).click()
  await expect(page.getByTestId('wn-sales')).toContainText('$93.00')
  await expect(page.getByTestId('wn-viewers')).toContainText('412')
  await expect(page.getByTestId('wn-ledger-empty')).toBeVisible()

  // Reports: the app asked Whatnot for its ledger when the shop was connected; Whatnot is
  // still building the file. Once it is ready, the net per show is there.
  await page.getByLabel('Sections').getByRole('link', { name: 'Reports' }).click()
  await expect(page.getByTestId('whatnot-ledger-state')).toContainText('building the report now')
  await expect(page.getByTestId('whatnot-ledger-read')).toHaveText('Check again')
  const at = new Date().toISOString()
  const built = await request.post('/api/whatnot/fake', {
    data: {
      finishReports: [
        { transaction_type: 'ORDER_EARNINGS', ledger_transaction_id: `${run}-1`, livestream_id: fake.shows[0], transaction_completed_at_utc: at, transaction_amount: '21.97', original_item_price: '25.00', commission_fee: '2.00', processing_fee: '1.03' },
        { transaction_type: 'SHIPPING_CHARGE', ledger_transaction_id: `${run}-2`, livestream_id: fake.shows[0], transaction_completed_at_utc: at, transaction_amount: '-5.25' },
      ],
    },
  })
  expect(built.status()).toBe(201)
  await page.getByTestId('whatnot-ledger-read').click()
  await expect(page.getByTestId('whatnot-ledger-state')).toContainText('2 ledger lines read')
  await expect(page.getByTestId('ledger-recent-net')).toHaveText('$16.72')
  await expect(page.getByTestId('ledger-shows')).toContainText(`Friday night ${run}`)
  await shot(page, '5-reports-ledger')
  await page.getByTestId('ledger-shows').getByRole('link').click()
  await expect(page.getByTestId('wn-ledger-net')).toHaveText('$16.72')
  await expect(page.getByTestId('wn-ledger-fees')).toContainText('Commission $2.00 · Payment processing $1.03')
  await shot(page, '6-show-on-whatnot')

  // The shop's Whatnot listings next to its own products. One carries the product's code and
  // links itself; the other names nothing the shop knows.
  await request.post('/api/whatnot/fake', {
    data: {
      products: [
        { title: 'Red bundle (live)', variants: [{ sku: 'red', priceCents: 2500, quantity: 9 }] },
        { title: 'Mystery crate', variants: [{ quantity: 2 }] },
      ],
    },
  })
  await page.getByLabel('Sections').getByRole('link', { name: 'Inventory' }).click()
  await page.getByRole('link', { name: 'Whatnot listings' }).click()
  await page.getByRole('button', { name: 'Read from Whatnot now' }).click()
  const listed = page.getByTestId('listing-red')
  await expect(listed).toContainText('matched by its code')
  await expect(listed).toContainText('differs') // Whatnot offers 9; the shop cannot sell that many
  const crate = page.getByTestId('listing-Mystery crate')
  await expect(crate).toContainText('Not linked yet')
  await crate.getByRole('button', { name: 'Not one of ours' }).click()
  await expect(crate).toContainText('Not one of ours')
  await expect(page.getByTestId('catalog-state')).toContainText('It never changes anything on Whatnot')
  await shot(page, '10-whatnot-listings')
  // Inventory shows Whatnot's count beside the shop's own.
  await page.getByRole('link', { name: '← Inventory' }).click()
  await expect(cell(page, 'RED', 'available').getByTestId('on-whatnot')).toHaveText('9 on Whatnot · differs')
  await shot(page, '11-inventory-on-whatnot')

  // The activity log says what happened, in words, with nothing internal.
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByText('Said the Whatnot listing “Mystery crate” is none of the shop\'s products')).toBeVisible()
  await expect(page.getByText("Applied Whatnot's orders to a show: 1 payment declined, 3 sales recorded")).toBeVisible()
  await expect(page.getByText('Asked Whatnot to make 2 shipping labels')).toBeVisible()
  await expect(page.getByText('Marked 1 order shipped: the carrier has them')).toBeVisible()
  await expect(page.getByText(`Connected the shop to Whatnot as @shop_${run}`)).toBeVisible()
})

// One product per listing, and no laptop: the winner, the item and the payment all come from
// Whatnot's orders. Buyers and products are made up (and unlike the test above's, so the two
// shops, which share the one in-memory Whatnot, never match each other's orders by title).
test('a show nobody records: from the first order to the slip, with listings linked on the way', async ({ page, request }) => {
  await signup(page, 'b')
  for (const [name, code] of [
    ['Ruby set', 'RUBY'],
    ['Sky set', 'SKY'],
    ['Moss set', 'MOSS'],
  ]) {
    await page.getByRole('link', { name: 'Products' }).click()
    await page.getByLabel('Product name').fill(name)
    await page.getByLabel('SKU code (optional)').fill(code)
    await page.getByRole('button', { name: 'Add product' }).click()
    await receive(page, code, 5)
  }

  // The shop sells one product per listing.
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByRole('radio', { name: /One product per listing/ }).check()
  await page.getByRole('button', { name: 'Save how you sell' }).click()

  // On Whatnot: two listings (one carries the product's code), and a show on air for forty minutes with two orders.
  const first = await request.post('/api/whatnot/fake', {
    data: {
      seller: { id: String(Date.now()), username: `shop_${run}b` },
      products: [{ title: `Ruby special ${run}`, variants: [{ sku: 'RUBY', quantity: 9 }] }, { title: `Sky special ${run}`, variants: [{ quantity: 9 }] }],
      shows: [{ title: `No laptop ${run}`, status: 'live', startedMinutesAgo: 40 }],
      orders: [
        { buyer: 'lani_b', items: [{ title: `Ruby special ${run}`, priceCents: 1200, product: 0 }], minutesAgo: 30 },
        { buyer: 'kat_b', items: [{ title: `Sky special ${run}`, priceCents: 1400, product: 1 }], minutesAgo: 25 },
      ],
    },
  })
  expect(first.status()).toBe(201)
  const show = ((await first.json()) as { shows: string[] }).shows[0]

  // Connect, apply orders as they arrive, and read now.
  await page.reload()
  await page.getByTestId('whatnot-connect').click()
  await expect(page.getByTestId('whatnot-state')).toContainText(`Connected as @shop_${run}b`)
  // The radio follows the saved answer, so it turns only once the server has it.
  await page.getByTestId('whatnot-mode').getByRole('radio', { name: /Apply as they arrive/ }).click()
  await expect(page.getByTestId('whatnot-mode').getByRole('radio', { name: /Apply as they arrive/ })).toBeChecked()
  const readNow = async () => {
    const done = page.waitForResponse((r) => r.url().endsWith('/api/whatnot/check') && r.request().method() === 'POST')
    await page.getByTestId('whatnot-check').click()
    expect((await done).ok()).toBe(true)
  }
  await readNow()
  // The set-up steps say this shop's shows need no laptop.
  await page.reload()
  await expect(page.getByTestId('no-laptop-needed')).toContainText('Your shows need no laptop.')

  // The listing with the product's code named its sale. The other one waits: "listing not linked".
  await page.goto('/app/orders?view=all')
  await expect(page.getByTestId('order-lani_b')).toContainText('Ruby set')
  const kat = page.getByTestId('order-kat_b')
  await expect(kat).toContainText('listing not linked')
  await kat.getByTestId('name-product').click()
  await expect(page.getByTestId('link-listing')).toBeChecked()
  const pick = async (name: string) => {
    const box = page.getByRole('dialog').getByRole('combobox')
    await box.fill(name)
    await page.getByRole('option', { name: new RegExp(name) }).first().click()
    await page.getByTestId('use-product').click()
  }
  await pick('Sky set')
  await expect(page.getByRole('status')).toContainText('now always uses it')
  await expect(page.getByTestId('order-kat_b')).toContainText('Sky set')

  // A listing made during the show, with two wins: the app has never seen it.
  const second = await request.post('/api/whatnot/fake', {
    data: {
      products: [{ title: `Moss drop ${run}`, variants: [{ quantity: 9 }] }],
      orders: [
        { showId: show, buyer: 'moe_b', items: [{ title: `Moss drop ${run}`, priceCents: 900, product: 0 }], minutesAgo: 6 },
        { showId: show, buyer: 'nia_b', items: [{ title: `Moss drop ${run}`, priceCents: 900, product: 0 }], minutesAgo: 4 },
      ],
    },
  })
  expect(second.status()).toBe(201)
  const moss = ((await second.json()) as { products: { id: string; variantId: string }[] }).products[0]
  await page.getByRole('link', { name: 'Settings' }).click()
  await readNow()
  await page.goto('/app/orders?view=all')
  await expect(page.getByTestId('order-nia_b')).toContainText('listing not linked')
  await page.getByTestId('order-moe_b').getByTestId('name-product').click()
  await pick('Moss set')
  // Naming one win linked the listing and named its other win in the show.
  await expect(page.getByRole('status')).toContainText('and 1 other lot')
  await expect(page.getByTestId('order-nia_b')).toContainText('Moss set')

  // The listing's next order names itself.
  const third = await request.post('/api/whatnot/fake', {
    data: { orders: [{ showId: show, buyer: 'ola_b', items: [{ title: `Moss drop ${run}`, priceCents: 900, productId: moss.id, variantId: moss.variantId }], minutesAgo: 1 }] },
  })
  expect(third.status()).toBe(201)
  await page.getByRole('link', { name: 'Settings' }).click()
  await readNow()
  await page.goto('/app/orders?view=all')
  await expect(page.getByTestId('order-ola_b')).toContainText('Moss set')
  await expect(page.getByText('listing not linked')).toHaveCount(0)

  // One product is changed on the order's own page: the old one goes back, the new one leaves.
  await page.getByTestId('order-kat_b').getByRole('button', { name: 'More for kat_b' }).click()
  await page.getByRole('menuitem', { name: 'Open the order and slip' }).click()
  await page.getByTestId('order-products').getByRole('button', { name: /Change Sky set/ }).click()
  const box = page.getByRole('dialog').getByRole('combobox')
  await box.fill('Ruby set')
  await page.getByRole('option', { name: /Ruby set/ }).first().click()
  await page.getByTestId('change-product-save').click()
  await expect(page.getByTestId('order-changed')).toContainText('is now Ruby set')
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(cell(page, 'RUBY', 'on-hand')).toHaveText('3')
  await expect(cell(page, 'SKY', 'on-hand')).toHaveText('5')
  await expect(cell(page, 'MOSS', 'on-hand')).toHaveText('2')

  // The show has a place of its own, says it needs no laptop, and its slips are ready to print.
  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await page.getByRole('row').filter({ hasText: `No laptop ${run}` }).getByRole('link').first().click()
  await expect(page.getByTestId('no-laptop')).toContainText('No laptop is needed for this show')
  await page.getByTestId('order-slips-link').click()
  await expect(page.getByTestId('slips-totals')).toContainText('5')
  for (const [buyer, product] of [
    ['lani_b', 'Ruby set'],
    ['kat_b', 'Ruby set'],
    ['moe_b', 'Moss set'],
    ['nia_b', 'Moss set'],
    ['ola_b', 'Moss set'],
  ]) {
    await expect(page.getByTestId(`slip-${buyer}`).getByTestId('slip-row')).toHaveCount(1)
    await expect(page.getByTestId(`slip-${buyer}`)).toContainText(product)
  }
  await shot(page, '8-no-laptop-slips')
})
