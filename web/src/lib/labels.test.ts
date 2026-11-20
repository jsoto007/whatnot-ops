import { saleLabel, salesLineUpWords, salesShippingWords } from './labels'

const base = { paidCents: 376379, grossCents: 376379, discountCents: 0, cancelledCents: 0, cancelledDiscountCents: 0, cancelledOrders: 0 }

test('sales that need no explaining say nothing', () => {
  expect(salesLineUpWords(base)).toBe('')
})

test('discounts and cancelled orders are what lies between Sales and Whatnot’s gross', () => {
  expect(salesLineUpWords({ ...base, grossCents: 381925, discountCents: 546, cancelledCents: 5000, cancelledOrders: 1 })).toBe(
    'Before discounts and cancellations: $3,819.25 (Sales plus $5.46 in discounts and $50.00 from 1 cancelled order).',
  )
})

test('a cancelled order that had a discount is said at its price before it, so the parts add up', () => {
  // paid $10.00, cancelled order $47.50 after a $2.50 discount: gross $60.00 = $10.00 + $50.00
  expect(salesLineUpWords({ paidCents: 1000, grossCents: 6000, discountCents: 0, cancelledCents: 4750, cancelledDiscountCents: 250, cancelledOrders: 1 })).toBe(
    'Before discounts and cancellations: $60.00 (Sales plus $50.00 from 1 cancelled order).',
  )
})

test('the shipping buyers paid is said beside the sales, with the two together', () => {
  // made-up figures: $69,991.30 of items and $13,324.88 of shipping on the same paid orders
  expect(salesShippingWords({ shippingCents: 1332488, withShippingCents: 6999130 + 1332488 })).toBe('Shipping buyers paid $13,324.88 · Sales with shipping $83,316.18')
})

test('no shipping, or a server that does not say it, says nothing', () => {
  expect(salesShippingWords({ shippingCents: 0, withShippingCents: 5045 })).toBe('')
  expect(salesShippingWords({})).toBe('')
})

test('a declined payment is never said', () => {
  expect(salesLineUpWords({ ...base, discountCents: 100, grossCents: 376479 })).not.toMatch(/declin|fail/i)
})

test('a sale settled with no product reads as paid on Whatnot, not as needing a decision', () => {
  expect(saleLabel({ status: 'detected', notOurs: true })).toBe('Paid on Whatnot · not one of our products')
  expect(saleLabel({ status: 'detected', notOurs: false })).toBe('needs a decision')
  expect(saleLabel({ status: 'paid' })).toBe('paid')
})
