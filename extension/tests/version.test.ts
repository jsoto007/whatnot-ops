import { afterEach, describe, expect, it, vi } from 'vitest'
import { postBatch } from '../src/upload/uploader'
import { deviceHeaders, extensionVersion, isNewer } from '../src/upload/version'

afterEach(() => vi.unstubAllGlobals())

describe('isNewer', () => {
  it('compares dotted numbers, not text', () => {
    expect(isNewer('1.10.0', '1.9.0')).toBe(true)
    expect(isNewer('1.5', '1.4.9')).toBe(true)
    expect(isNewer('1.5.0', '1.5')).toBe(false)
    expect(isNewer('1.4.0', '1.5.0')).toBe(false)
    expect(isNewer('2.0.0.1', '2.0.0')).toBe(true)
  })

  it('never claims an update from a missing or odd version', () => {
    expect(isNewer(null, '1.5.0')).toBe(false)
    expect(isNewer('1.6.0', '')).toBe(false)
    expect(isNewer('1.6.0-beta', '1.5.0')).toBe(false)
  })
})

describe('device headers', () => {
  it('carry the running version so the web app knows which laptops are behind', async () => {
    vi.stubGlobal('chrome', { runtime: { getManifest: () => ({ version: '1.5.0' }) } })
    expect(extensionVersion()).toBe('1.5.0')
    expect(deviceHeaders('wn_key')).toEqual({ Authorization: 'Bearer wn_key', 'X-Extension-Version': '1.5.0' })
    const fetchFn = vi.fn(async () => new Response('{}', { status: 200 }))
    await postBatch({ serverUrl: 'https://ops.test', apiKey: 'wn_key' }, { version: 1, show: { id: 's', startedAt: 's' }, rows: [] }, fetchFn)
    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect((init.headers as Record<string, string>)['X-Extension-Version']).toBe('1.5.0')
  })

  it('leave the version out where there is no extension runtime', () => {
    expect(extensionVersion()).toBe('')
    expect(deviceHeaders('wn_key')).toEqual({ Authorization: 'Bearer wn_key' })
  })
})
