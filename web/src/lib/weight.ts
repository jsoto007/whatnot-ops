/**
 * One unit's weight. The server keeps ounces to 0.01 oz (`Sku.weightOz`); people type
 * ounces or pounds, and the page shows it the way a US postal scale reads.
 */
export type WeightUnit = 'oz' | 'lb'

/** 150 lb, the heaviest parcel the big carriers take (the server refuses more). */
export const MAX_WEIGHT_OZ = 2400

const hundredths = (oz: number) => Math.round(oz * 100)

/** 4.5 → "4.5 oz", 20 → "1 lb 4 oz", 32 → "2 lb". */
export function fmtWeight(oz: number) {
  const h = hundredths(oz)
  const lb = Math.floor(h / 1600)
  const rest = (h % 1600) / 100
  return [lb ? `${lb} lb` : '', rest ? `${rest} oz` : ''].filter(Boolean).join(' ') || '0 oz'
}

/** What was typed, in ounces to 0.01 oz: null when blank, NaN when it is not a number. */
export function parseWeight(text: string, unit: WeightUnit): number | null {
  const t = text.trim()
  if (!t) return null
  if (!/^(\d+\.?\d*|\.\d+)$/.test(t)) return NaN
  const n = parseFloat(t)
  return hundredths(unit === 'lb' ? n * 16 : n) / 100
}

/** Why a typed weight cannot be saved, or null when it can (blank is fine: not weighed). */
export function weightProblem(oz: number | null) {
  if (oz === null) return null
  if (Number.isNaN(oz)) return 'Weight is a number, like 4.5 or 1.25.'
  if (oz <= 0) return 'Weight must be more than 0.'
  if (oz > MAX_WEIGHT_OZ) return 'That is over 150 lb; check the number.'
  return null
}

/** A saved weight back in the box: whole pounds or 0.01 lb steps as lb, anything else as oz. */
export function weightDraft(oz: number | null): { text: string; unit: WeightUnit } {
  if (oz === null) return { text: '', unit: 'oz' }
  const h = hundredths(oz)
  // 0.01 lb = 0.16 oz, i.e. 16 hundredths of an ounce.
  if (h >= 1600 && h % 16 === 0) return { text: String(h / 1600), unit: 'lb' }
  return { text: String(h / 100), unit: 'oz' }
}
