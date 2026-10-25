import { IDBFactory } from 'fake-indexeddb'
import { KV_RECORDING, kvGet, resetDbForTests } from '../src/storage/db'
import { claimRecording, endRecording, forgetRecording, resumeFor, type Tabs } from '../src/storage/recording'
import { showPage, type ChatSpot } from '../src/shared/messages'

function fakeTabs(recordingTabs: number[] = []) {
  const log: string[] = []
  const tabs: Tabs = {
    isRecording: async (id) => recordingTabs.includes(id),
    release: (id, show) => void log.push(`release ${id} ${show}`),
    keepAlive: (id, keep) => void log.push(`${keep ? 'keep' : 'free'} ${id}`),
  }
  return { tabs, log }
}

const spot: ChatSpot = { selectors: ['div.msg'], page: '/live/abc' }

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
  resetDbForTests()
})

describe('recording ownership', () => {
  it('the same tab carries on after a reload, and keeps Chrome from discarding it', async () => {
    const { tabs, log } = fakeTabs()
    await claimRecording('show1', spot, 7, tabs)
    expect(log).toEqual(['keep 7'])
    expect(await resumeFor('show1', 7, tabs)).toEqual({ recording: spot })
    expect(await resumeFor('other-show', 7, tabs)).toEqual({})
  })

  it('a new tab takes over only when the old one is no longer recording', async () => {
    const busy = fakeTabs([7])
    await claimRecording('show1', spot, 7, busy.tabs)
    expect(await resumeFor('show1', 9, busy.tabs)).toEqual({ elsewhere: true })
    expect((await kvGet<{ tabId: number }>(KV_RECORDING))?.tabId).toBe(7)

    const gone = fakeTabs([]) // tab 7 was closed, or left Whatnot
    expect(await resumeFor('show1', 9, gone.tabs)).toEqual({ recording: spot })
    expect(gone.log).toEqual(['release 7 show1', 'free 7', 'keep 9'])
    expect((await kvGet<{ tabId: number }>(KV_RECORDING))?.tabId).toBe(9)
  })

  it('Stop ends it, but a tab that was taken over cannot stop the new one', async () => {
    const { tabs, log } = fakeTabs()
    await claimRecording('show1', spot, 9, tabs)
    await endRecording(7, tabs)
    expect(await resumeFor('show1', 9, tabs)).toEqual({ recording: spot })
    await endRecording(9, tabs)
    expect(log.at(-1)).toBe('free 9')
    expect(await resumeFor('show1', 9, tabs)).toEqual({})
  })

  it('Start fresh forgets it', async () => {
    const { tabs } = fakeTabs()
    await claimRecording('show1', spot, 9, tabs)
    await forgetRecording(tabs)
    expect(await kvGet(KV_RECORDING)).toBeUndefined()
  })
})

describe('showPage', () => {
  it('keeps the part of the path that names the show', () => {
    expect(showPage('/live/abc-123')).toBe('/live/abc-123')
    expect(showPage('/live/abc-123/')).toBe('/live/abc-123')
    expect(showPage('/live/abc-123/shop')).toBe('/live/abc-123')
    expect(showPage('/live/other')).not.toBe(showPage('/live/abc-123'))
    expect(showPage('/')).toBe('/')
  })
})
