/** FNV-1a 64-bit as 16 hex chars. Fast, dependency-free, stable across devices. */
export function fnv1a64(str: string): string {
  let h = 0xcbf29ce484222325n
  const prime = 0x100000001b3n
  for (const byte of new TextEncoder().encode(str)) {
    h ^= BigInt(byte)
    h = (h * prime) & 0xffffffffffffffffn
  }
  return h.toString(16).padStart(16, '0')
}

export const ID_BUCKET_MS = 2000

/**
 * Idempotency key for a captured line: the same message seen again within the
 * same 2-second bucket (a re-render, a retry) gets the same id; a genuine
 * repeat later gets a new one.
 */
export function rowId(showId: string, username: string, body: string, capturedAtMs: number): string {
  const bucket = Math.floor(capturedAtMs / ID_BUCKET_MS)
  return fnv1a64(`${showId}\u0000${username}\u0000${body}\u0000${bucket}`)
}

/**
 * Idempotency key for a win read from the player: no time bucket, because the same
 * "won!" stays on screen for seconds and is read again after a reload. `occurrence`
 * tells a genuine second win of the same item, by the same buyer, at the same price apart.
 */
export function winId(showId: string, key: string, occurrence: number): string {
  return fnv1a64(`${showId}\u0000player\u0000${key}\u0000${occurrence}`)
}
