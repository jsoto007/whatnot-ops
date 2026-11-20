import { salesLineUpWords } from './labels'

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

test('a declined payment is never said', () => {
  expect(salesLineUpWords({ ...base, discountCents: 100, grossCents: 376479 })).not.toMatch(/declin|fail/i)
})
