import type { ChatSpot } from '../shared/messages'
import { KV_RECORDING, type RecordingRef, kvDelete, kvGet, kvSet } from './db'

/**
 * Which tab records which show, kept by the background so a recording outlives
 * the page: once started it carries on after a reload, a discarded tab coming
 * back or an extension update, until someone presses Stop. One tab at a time,
 * so two tabs never number the same show's lines twice.
 */
export interface Tabs {
  /** Does the tab answer that it is recording right now? False when it is closed or off Whatnot. */
  isRecording(tabId: number): Promise<boolean>
  /** Tell a tab that another one took its show over. */
  release(tabId: number, showId: string): void
  /** Keep Chrome from discarding the tab to save memory (it would stop recording). */
  keepAlive(tabId: number, keep: boolean): void
}

/** `tabId` records `showId` from `spot` from now on; a tab that had it is told to let go. */
export async function claimRecording(showId: string, spot: ChatSpot, tabId: number | undefined, tabs: Tabs): Promise<void> {
  const prev = await kvGet<RecordingRef>(KV_RECORDING)
  if (prev?.tabId !== undefined && prev.tabId !== tabId) {
    tabs.release(prev.tabId, prev.showId)
    tabs.keepAlive(prev.tabId, false)
  }
  await kvSet(KV_RECORDING, { showId, spot, ...(tabId !== undefined ? { tabId } : {}) } satisfies RecordingRef)
  if (tabId !== undefined) tabs.keepAlive(tabId, true)
}

/** Stop in `tabId`. A tab that was taken over cannot stop the tab that has the show now. */
export async function endRecording(tabId: number | undefined, tabs: Tabs): Promise<void> {
  const prev = await kvGet<RecordingRef>(KV_RECORDING)
  if (!prev || (prev.tabId !== undefined && prev.tabId !== tabId)) return
  await kvDelete(KV_RECORDING)
  if (prev.tabId !== undefined) tabs.keepAlive(prev.tabId, false)
}

/** Start fresh / Clear: nothing is recording any more. */
export async function forgetRecording(tabs: Tabs): Promise<void> {
  const prev = await kvGet<RecordingRef>(KV_RECORDING)
  await kvDelete(KV_RECORDING)
  if (prev?.tabId !== undefined) tabs.keepAlive(prev.tabId, false)
}

/**
 * A page loaded in `tabId` and found `showId` saved. If that show was still
 * recording, the same tab (reloaded) carries on, and so does a new tab when
 * the old one is gone or no longer recording. While another tab is still
 * recording it, this one only hears `elsewhere`.
 */
export async function resumeFor(
  showId: string,
  tabId: number | undefined,
  tabs: Tabs,
): Promise<{ recording?: ChatSpot; elsewhere?: boolean }> {
  const prev = await kvGet<RecordingRef>(KV_RECORDING)
  if (!prev || prev.showId !== showId) return {}
  const mine = prev.tabId === undefined || prev.tabId === tabId
  if (!mine && (await tabs.isRecording(prev.tabId!))) return { elsewhere: true }
  await claimRecording(showId, prev.spot, tabId, tabs)
  return { recording: prev.spot }
}
