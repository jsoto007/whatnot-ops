import type { ReviewItem } from './api'
import { groupByShow, NO_SHOW } from './reviewGroups'

const item = (id: number, showId: number | null, showDate: string | null, reason = 'unmatched_item'): ReviewItem => ({
  id,
  showId,
  showDate,
  reason,
  summary: '',
  source: 'rules',
  status: 'open',
  proposal: null,
  message: null,
  context: [],
  sale: null,
  createdAt: '',
})

test('decisions are grouped by show, newest show first, with a count per reason', () => {
  const groups = groupByShow([
    item(1, 3, '2026-10-20'),
    item(2, 5, '2026-10-24', 'low_confidence'),
    item(3, 3, '2026-10-20'),
    item(4, 5, '2026-10-24'),
    item(5, 3, '2026-10-20', 'no_payment_record'),
  ])
  expect(groups.map((g) => g.key)).toEqual(['5', '3'])
  expect(groups[1].items.map((i) => i.id)).toEqual([1, 3, 5])
  expect([...groups[1].byReason.entries()]).toEqual([
    ['unmatched_item', 2],
    ['no_payment_record', 1],
  ])
})

test('two shows on the same date fall back to the newer id; decisions without a show come last', () => {
  const groups = groupByShow([item(1, null, null), item(2, 7, '2026-10-24'), item(3, 9, '2026-10-24')])
  expect(groups.map((g) => g.key)).toEqual(['9', '7', NO_SHOW])
  expect(groups[2].showDate).toBeNull()
})
