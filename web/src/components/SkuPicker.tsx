import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Icon } from './Icon'
import { searchSkus, skuLabel, type SearchableSku } from '../lib/skuSearch'

/** How many matches the list shows before asking for another word. */
const LIMIT = 12

interface Props<T extends SearchableSku> {
  skus: T[]
  /** The chosen SKU's id, '' for none. */
  value: string
  onChange: (id: string) => void
  /** The box's accessible name, e.g. "SKU for review 12". */
  label: string
  showAvailable?: boolean
}

/**
 * A search box that finds a SKU by product, name, code or chat name and lists the best matches
 * to pick from, by mouse or arrow keys + Enter. Once picked, the box shows the SKU's label;
 * typing again or × clears the pick.
 */
export function SkuPicker<T extends SearchableSku>({ skus, value, onChange, label, showAvailable = true }: Props<T>) {
  const id = useId()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const chosen = value ? skus.find((s) => String(s.id) === value) : undefined
  const { hits, total } = searchSkus(skus, query, LIMIT)
  const listId = `${id}-list`
  const optionId = (i: number) => `${id}-option-${i}`
  const shown = open && !chosen
  const current = Math.min(active, Math.max(hits.length - 1, 0))

  useEffect(() => {
    if (shown) document.getElementById(`${id}-option-${current}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [shown, current, id])

  const pick = (s: T) => {
    onChange(String(s.id))
    setQuery('')
    setOpen(false)
  }
  const clear = () => {
    onChange('')
    setQuery('')
    setActive(0)
    input.current?.focus()
  }
  const type = (text: string) => {
    if (chosen) onChange('')
    setQuery(text)
    setActive(0)
    setOpen(true)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (shown) setActive(Math.min(current + 1, hits.length - 1))
      else if (!chosen) setOpen(true)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(Math.max(current - 1, 0))
    } else if (e.key === 'Enter') {
      if (shown && hits[current]) {
        e.preventDefault()
        pick(hits[current])
      }
    } else if (e.key === 'Escape') {
      if (shown) setOpen(false)
      else if (chosen) clear()
    }
  }
  const note = !skus.length ? 'No products yet.' : !hits.length ? `Nothing matches “${query.trim()}”.` : total > hits.length ? `${total - hits.length} more · keep typing to narrow it down` : null

  return (
    <div className={'sku-picker' + (shown ? ' open' : '')}>
      <div className="search">
        <Icon name="search" size={16} />
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-label={label}
          aria-expanded={shown}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={shown && hits[current] ? optionId(current) : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder="Search by product, SKU or chat name"
          value={chosen ? skuLabel(chosen) : query}
          onChange={(e) => type(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
        />
        {(chosen || query) && (
          <button type="button" className="search-clear" onClick={clear} aria-label="Clear SKU">
            ×
          </button>
        )}
      </div>
      {shown && (
        // Mouse-down is swallowed so the box keeps focus (and the list stays open) until the click lands.
        <div className="sku-list" onMouseDown={(e) => e.preventDefault()}>
          <ul role="listbox" id={listId} aria-label="Matching SKUs">
            {hits.map((s, i) => (
              <li key={s.id} id={optionId(i)} role="option" aria-selected={i === current} onMouseMove={() => setActive(i)} onClick={() => pick(s)}>
                <span className="sku-name">
                  <b>{s.productName}</b>
                  {s.name ? ` ${s.name}` : ''}
                </span>
                <span className="sku-meta">
                  {s.code}
                  {showAvailable ? ` · ${s.available} available` : ''}
                  {s.active === false ? ' · retired' : ''}
                </span>
              </li>
            ))}
          </ul>
          {note && <div className="sku-note">{note}</div>}
        </div>
      )}
    </div>
  )
}
