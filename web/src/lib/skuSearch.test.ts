import { searchSkus, skuLabel, type SearchableSku } from './skuSearch'

const sku = (id: number, code: string, productName: string, name = '', extra: Partial<SearchableSku> = {}): SearchableSku => ({ id, code, productName, name, available: 3, aliases: [], ...extra })
const CATALOGUE = [
  sku(1, 'RED', 'Red bundle'),
  sku(2, 'HOLO', 'Holo slab', 'Charizard'),
  sku(3, 'RED-XL', 'Red bundle', 'XL', { aliases: [{ alias: 'mystery box' }] }),
  sku(4, 'PKM-151', 'Pokémon 151 booster'),
  sku(5, 'OLD', 'Old bundle', '', { active: false }),
]
const ids = (r: { hits: SearchableSku[] }) => r.hits.map((s) => s.id)

test('matches the product, the SKU name, the code and chat names; every word typed must match', () => {
  expect(ids(searchSkus(CATALOGUE, 'red', 10))).toEqual([1, 3])
  expect(ids(searchSkus(CATALOGUE, 'chariz', 10))).toEqual([2])
  expect(ids(searchSkus(CATALOGUE, 'mystery', 10))).toEqual([3])
  expect(ids(searchSkus(CATALOGUE, 'red xl', 10))).toEqual([3])
  expect(ids(searchSkus(CATALOGUE, 'red slab', 10))).toEqual([])
})

test('case and accents do not matter', () => {
  expect(ids(searchSkus(CATALOGUE, 'POKEMON', 10))).toEqual([4])
  expect(ids(searchSkus(CATALOGUE, 'pokémon 151', 10))).toEqual([4])
})

test('the exact code wins, then codes and words starting with the text, then anything containing it', () => {
  const list = [sku(1, 'B-RED', 'Something'), sku(2, 'RED', 'Elsewhere'), sku(3, 'A1', 'Bordered frame'), sku(4, 'REDO', 'Zed')]
  expect(ids(searchSkus(list, 'red', 10))).toEqual([2, 4, 3, 1])
})

test('retired SKUs come last, the list is cut at the limit and the total still counts every match', () => {
  const all = searchSkus(CATALOGUE, '', 3)
  expect(all.total).toBe(5)
  expect(ids(all)).toEqual([2, 4, 1])
  expect(ids(searchSkus(CATALOGUE, 'bundle', 10))).toEqual([1, 3, 5])
})

test('a label names the product, the variant when there is one, and the code', () => {
  expect(skuLabel(sku(2, 'HOLO', 'Holo slab', 'Charizard'))).toBe('Holo slab · Charizard (HOLO)')
  expect(skuLabel(sku(1, 'RED', 'Red bundle'))).toBe('Red bundle (RED)')
})
