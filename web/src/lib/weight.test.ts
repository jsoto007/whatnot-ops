import { fmtWeight, parseWeight, weightDraft, weightProblem } from './weight'

test('weights read like a postal scale: ounces under a pound, then pounds and ounces', () => {
  expect(fmtWeight(4.5)).toBe('4.5 oz')
  expect(fmtWeight(0.25)).toBe('0.25 oz')
  expect(fmtWeight(16)).toBe('1 lb')
  expect(fmtWeight(20)).toBe('1 lb 4 oz')
  expect(fmtWeight(21.33)).toBe('1 lb 5.33 oz')
  expect(fmtWeight(32)).toBe('2 lb')
})

test('typed weights become ounces to 0.01 oz; blank is "not weighed", junk is NaN', () => {
  expect(parseWeight('4.5', 'oz')).toBe(4.5)
  expect(parseWeight(' 1.25 ', 'lb')).toBe(20)
  expect(parseWeight('1.333', 'lb')).toBe(21.33)
  expect(parseWeight('.5', 'oz')).toBe(0.5)
  expect(parseWeight('3.', 'oz')).toBe(3)
  expect(parseWeight('', 'oz')).toBeNull()
  expect(parseWeight('  ', 'lb')).toBeNull()
  expect(parseWeight('1.2.3', 'oz')).toBeNaN()
  expect(parseWeight('.', 'oz')).toBeNaN()
  expect(parseWeight('-2', 'oz')).toBeNaN()
})

test('only a positive weight up to 150 lb can be saved', () => {
  expect(weightProblem(null)).toBeNull()
  expect(weightProblem(4.5)).toBeNull()
  expect(weightProblem(2400)).toBeNull()
  expect(weightProblem(0)).toMatch(/more than 0/)
  expect(weightProblem(parseWeight('0.004', 'oz'))).toMatch(/more than 0/)
  expect(weightProblem(2400.01)).toMatch(/150 lb/)
  expect(weightProblem(NaN)).toMatch(/number/)
})

test('a saved weight goes back in the box in the unit that reads cleanly', () => {
  expect(weightDraft(null)).toEqual({ text: '', unit: 'oz' })
  expect(weightDraft(4.5)).toEqual({ text: '4.5', unit: 'oz' })
  expect(weightDraft(20)).toEqual({ text: '1.25', unit: 'lb' })
  expect(weightDraft(32)).toEqual({ text: '2', unit: 'lb' })
  expect(weightDraft(21.33)).toEqual({ text: '21.33', unit: 'oz' })
  // Round trip: what the box shows parses back to the same ounces.
  for (const oz of [0.25, 4.5, 15.99, 16, 20, 21.33, 37.6, 2400]) {
    const d = weightDraft(oz)
    expect(parseWeight(d.text, d.unit)).toBe(oz)
  }
})
