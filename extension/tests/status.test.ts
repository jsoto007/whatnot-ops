import { declineBody } from '../src/capture/player'
import type { CapturedRow } from '../src/capture/types'
import { dotState, stockNotice, uploadNote, winNotice } from '../src/content/status'
import type { UploadStatus } from '../src/shared/messages'

const ok: UploadStatus = { configured: true, pending: 0, parked: 0 }
const off: UploadStatus = { configured: false, pending: 0, parked: 0 }
const rec = { picking: false, recording: true, watching: true, upload: ok }

describe('the dot', () => {
  it('is green when recording, watching the chat and uploading', () => {
    expect(dotState(rec)).toBe('good')
    expect(dotState({ ...rec, upload: null })).toBe('good') // before the background has said anything
    expect(dotState({ ...rec, upload: { ...ok, pending: 12 } })).toBe('good') // a few lines on their way is normal
  })

  it('is amber while something sorts itself out: chat off screen, server unreachable, lines set aside', () => {
    expect(dotState({ ...rec, watching: false })).toBe('warn')
    expect(dotState({ ...rec, upload: { ...ok, pending: 40, trouble: 'offline', lastError: 'Failed to fetch' } })).toBe('warn')
    expect(dotState({ ...rec, upload: { ...ok, parked: 3, trouble: 'rejected' } })).toBe('warn')
  })

  it('is red when the host has to act: not connected, refused, or the extension was reloaded', () => {
    expect(dotState({ ...rec, upload: off })).toBe('bad')
    expect(dotState({ ...rec, upload: { ...ok, pending: 40, trouble: 'auth' } })).toBe('bad')
    expect(dotState({ ...rec, cutOff: true })).toBe('bad')
    expect(dotState({ ...rec, watching: false, upload: off })).toBe('bad') // red beats amber
  })

  it('is blue while waiting for the click on a message, grey when not recording', () => {
    expect(dotState({ ...rec, picking: true, recording: false })).toBe('picking')
    expect(dotState({ ...rec, recording: false })).toBe('idle')
    expect(dotState({ picking: false, recording: false, watching: false, upload: off })).toBe('idle') // not connected matters only while recording
  })
})

describe('the upload note', () => {
  it('says how uploads are doing in words', () => {
    expect(uploadNote(null)).toBe('')
    expect(uploadNote(ok)).toBe(' · uploaded')
    expect(uploadNote({ ...ok, pending: 12 })).toBe(' · 12 to upload')
    expect(uploadNote(off)).toBe(' · not uploading (connect this laptop in Settings)')
    expect(uploadNote({ ...ok, pending: 40, trouble: 'offline', lastError: 'Failed to fetch' })).toBe(" · 40 to upload — can't reach Sales Ops, retrying")
    expect(uploadNote({ ...ok, parked: 3, trouble: 'rejected', lastError: '3 lines set aside (the server refused them): bad row' })).toBe(
      ' · 3 lines set aside (Sales Ops refused them)',
    )
    expect(uploadNote({ ...ok, pending: 2, parked: 3, trouble: 'rejected' })).toBe(' · 2 to upload · 3 lines set aside (Sales Ops refused them)')
  })

  it('never puts the key, a header or the server\'s own words on screen', () => {
    const note = uploadNote({ ...ok, pending: 40, trouble: 'auth', lastError: 'server rejected the API key' })
    expect(note).toBe(' · 40 to upload — Sales Ops no longer recognises this laptop (reconnect in Settings)')
    expect(note).not.toMatch(/API key|server/i)
  })

  it('falls back to the background\'s sentence when an older background gives no kind', () => {
    expect(uploadNote({ ...ok, pending: 5, lastError: 'server returned 503' })).toBe(' · 5 to upload — server returned 503')
  })
})

describe('notices', () => {
  const row = (body: string, source?: 'player'): CapturedRow => ({
    id: 'r1',
    showId: '2026-11-09T00:00:00.000Z',
    username: 'whatnot',
    body,
    rawText: body,
    capturedAt: '2026-11-09T01:00:00.000Z',
    seq: 1,
    ...(source ? { source } : {}),
  })

  it('a win from the player becomes "who won · price", the lot under it', () => {
    expect(winNotice(row('tiffybid won the auction for $1 BEAUTY PULLS #1290 for $19.00', 'player'))).toEqual({
      kind: 'win',
      title: 'tiffybid won · $19.00',
      detail: '$1 BEAUTY PULLS #1290',
    })
  })

  it('a chat line that looks like a win is not one, and neither is a decline', () => {
    expect(winNotice(row('tiffybid won the auction for X for $19.00'))).toBeNull()
    expect(winNotice(row(declineBody({ winner: 'tiffybid', title: 'X', priceCents: 1900 }), 'player'))).toBeNull()
  })

  it('stock alerts: amber with what is left, red when sold out, cleared together', () => {
    const item = { skuId: 1, code: 'RB-01', name: 'Red bandana', available: 2 }
    expect(stockNotice({ item, text: '2 left: Red bandana (RB-01)', urgent: false })).toEqual({
      kind: 'low',
      title: '2 left',
      detail: 'Red bandana · RB-01',
      urgent: false,
      group: 'stock',
    })
    expect(stockNotice({ item: { ...item, available: 0 }, text: 'sold out: Red bandana (RB-01)', urgent: true })).toEqual({
      kind: 'out',
      title: 'Sold out',
      detail: 'Red bandana · RB-01',
      urgent: true,
      group: 'stock',
    })
    expect(stockNotice({ item: { ...item, available: -2 }, text: 'oversold by 2: Red bandana (RB-01)', urgent: true }).title).toBe('Sold out')
  })
})
