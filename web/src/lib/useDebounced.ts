import { useEffect, useState } from 'react'

/** `value` once it has stopped changing for `ms`: a search box asks the server once per pause, not per keystroke. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return settled
}
