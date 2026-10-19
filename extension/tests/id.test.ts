import { fnv1a64, rowId, ID_BUCKET_MS } from '../src/capture/id'

describe('fnv1a64', () => {
  it('is deterministic, 16 hex chars, and sensitive to input', () => {
    expect(fnv1a64('abc')).toBe(fnv1a64('abc'))
    expect(fnv1a64('abc')).toMatch(/^[0-9a-f]{16}$/)
    expect(fnv1a64('abc')).not.toBe(fnv1a64('abd'))
    expect(fnv1a64('')).toBe('cbf29ce484222325')
  })
  it('hashes unicode by UTF-8 bytes', () => {
    expect(fnv1a64('🔥')).not.toBe(fnv1a64('?'))
  })
})

describe('rowId', () => {
  const t = 1_700_000_000_000
  it('is the same for a re-render inside the bucket and differs across buckets', () => {
    expect(rowId('s', 'u', 'hi', t)).toBe(rowId('s', 'u', 'hi', t + 500))
    expect(rowId('s', 'u', 'hi', t)).not.toBe(rowId('s', 'u', 'hi', t + ID_BUCKET_MS))
  })
  it('differs by show, user and body', () => {
    expect(rowId('s1', 'u', 'hi', t)).not.toBe(rowId('s2', 'u', 'hi', t))
    expect(rowId('s', 'u1', 'hi', t)).not.toBe(rowId('s', 'u2', 'hi', t))
    expect(rowId('s', 'u', 'hi', t)).not.toBe(rowId('s', 'u', 'ho', t))
  })
})
