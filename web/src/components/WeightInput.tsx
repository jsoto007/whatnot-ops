import type { WeightUnit } from '../lib/weight'

/** A number and its unit (oz / lb) as one control. The parent keeps both as typed. */
export function WeightInput({
  id,
  text,
  unit,
  onText,
  onUnit,
  label,
  invalid,
}: {
  id: string
  text: string
  unit: WeightUnit
  onText: (v: string) => void
  onUnit: (u: WeightUnit) => void
  label: string
  invalid?: boolean
}) {
  return (
    <span className="weight-input">
      <input
        id={id}
        value={text}
        onChange={(e) => onText(e.target.value.replace(/[^\d.]/g, '').slice(0, 8))}
        inputMode="decimal"
        placeholder="0"
        aria-label={label}
        aria-invalid={invalid || undefined}
      />
      <select value={unit} onChange={(e) => onUnit(e.target.value as WeightUnit)} aria-label={`${label}: unit`}>
        <option value="oz">oz</option>
        <option value="lb">lb</option>
      </select>
    </span>
  )
}
