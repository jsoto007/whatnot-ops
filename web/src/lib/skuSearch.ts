/** What the SKU picker searches: a SKU with its product's name and the chat names staff taught it. */
export interface SearchableSku {
  id: number
  code: string
  name: string
  productName: string
  available: number
  active?: boolean
  aliases?: { alias: string }[]
}

/** Lower-case without accents, so "pokemon" finds "Pokémon". */
const fold = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
const words = (s: string) => fold(s).split(/\s+/).filter(Boolean)

/** One line naming a SKU: the product, its variant when it has one, and the code. */
export const skuLabel = (s: SearchableSku) => `${s.productName}${s.name ? ` · ${s.name}` : ''} (${s.code})`

/**
 * The SKUs whose code, product, name or chat names contain every word typed, best first: the
 * exact code, then codes and words that start with what was typed, then anything containing it;
 * ties by product, then variant name, then code. Retired SKUs come after live ones. `total`
 * counts every match, `hits` is the first `limit` of them.
 */
export function searchSkus<T extends SearchableSku>(skus: T[], query: string, limit: number): { hits: T[]; total: number } {
  const typed = words(query)
  const scored: { sku: T; score: number }[] = []
  for (const sku of skus) {
    const code = fold(sku.code)
    const text = [code, sku.productName, sku.name, ...(sku.aliases ?? []).map((a) => a.alias)].map(fold).join(' ')
    const parts = text.split(/\s+/)
    let score = sku.active === false ? 100 : 0
    let matches = true
    for (const w of typed) {
      if (code === w) continue
      if (code.startsWith(w)) score += 1
      else if (parts.some((p) => p.startsWith(w))) score += 2
      else if (text.includes(w)) score += 3
      else {
        matches = false
        break
      }
    }
    if (matches) scored.push({ sku, score })
  }
  scored.sort((a, b) => a.score - b.score || a.sku.productName.localeCompare(b.sku.productName) || a.sku.name.localeCompare(b.sku.name) || a.sku.code.localeCompare(b.sku.code))
  return { hits: scored.slice(0, limit).map((s) => s.sku), total: scored.length }
}
