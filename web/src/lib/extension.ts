/**
 * Chat Scraper on this laptop. From 1.5.0 the extension's download names this
 * web app in its manifest: a content script (portal.js) answers `who` with the
 * extension's id and version, and the background takes `hello` / `reload`
 * through chrome.runtime.sendMessage (externally_connectable). Older builds
 * answer nothing, so `useThisLaptop()` stays null for them.
 */
import { useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from './api'

export interface ExtensionInfo {
  available: boolean
  version: string | null
  serverUrl: string | null
}

export const useExtensionInfo = (enabled = true) =>
  useQuery({ queryKey: ['extension'], queryFn: () => apiGet<ExtensionInfo>('/api/extension'), enabled, staleTime: 5 * 60_000 })

const parts = (v: string): number[] | null => (/^\d{1,5}(\.\d{1,5}){0,3}$/.test(v) ? v.split('.').map(Number) : null)

/** True when `latest` is a later version than `current` (dotted numbers, as Chrome writes them). */
export function isNewer(latest: string | null | undefined, current: string | null | undefined): boolean {
  const a = parts(latest ?? '')
  const b = parts(current ?? '')
  if (!a || !b) return false
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0)
    if (d) return d > 0
  }
  return false
}

export interface ThisLaptop {
  id: string
  version: string
}

// One listener for the whole app: the banner and Settings → Laptops read the same answer.
let laptop: ThisLaptop | null = null
const subscribers = new Set<() => void>()

function onMessage(e: MessageEvent) {
  const d = e.data as { source?: unknown; type?: unknown; id?: unknown; version?: unknown } | null
  if (e.source !== window || e.origin !== location.origin || d?.source !== 'chat-scraper' || d.type !== 'here') return
  if (typeof d.id !== 'string' || !/^[a-p]{32}$/.test(d.id) || typeof d.version !== 'string' || !parts(d.version)) return
  if (laptop?.id === d.id && laptop.version === d.version) return
  laptop = { id: d.id, version: d.version }
  subscribers.forEach((f) => f())
}

function subscribe(f: () => void) {
  if (!subscribers.size) {
    window.addEventListener('message', onMessage)
    window.postMessage({ source: 'sales-ops', type: 'who' }, location.origin)
  }
  subscribers.add(f)
  return () => {
    subscribers.delete(f)
    if (!subscribers.size) window.removeEventListener('message', onMessage)
  }
}

/** The Chat Scraper running on this laptop, or null (none, a build before 1.5.0, another browser). */
export function useThisLaptop(): ThisLaptop | null {
  return useSyncExternalStore(subscribe, () => laptop)
}

/** After a reload the background answers with the new version; keep the banner in step. */
export function setLaptopVersion(id: string, version: string) {
  if (laptop?.id !== id || laptop.version === version) return
  laptop = { id, version }
  subscribers.forEach((f) => f())
}

/** Tests only. */
export function resetThisLaptop() {
  laptop = null
  subscribers.forEach((f) => f())
}

export interface ExtensionHello {
  version: string
  filesVersion: string | null
  recording: boolean
}

interface Runtime {
  sendMessage(id: string, msg: unknown, callback: (reply: unknown) => void): void
  lastError?: { message?: string }
}

/** Ask the extension `id` something. Rejects when Chrome cannot reach it (not installed, reloading, other shop). */
export function askExtension<T>(id: string, msg: { cmd: 'hello' } | { cmd: 'reload' }, timeoutMs = 3000): Promise<T> {
  const runtime = (globalThis as { chrome?: { runtime?: Runtime } }).chrome?.runtime
  if (!runtime?.sendMessage) return Promise.reject(new Error('Chat Scraper cannot be reached from this page'))
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Chat Scraper did not answer')), timeoutMs)
    try {
      runtime.sendMessage(id, msg, (reply) => {
        clearTimeout(timer)
        const err = runtime.lastError
        const r = reply as { error?: string } | undefined
        if (err || !r) reject(new Error(err?.message || 'Chat Scraper did not answer'))
        else if (r.error) reject(new Error(r.error))
        else resolve(reply as T)
      })
    } catch (e) {
      clearTimeout(timer)
      reject(e instanceof Error ? e : new Error(String(e)))
    }
  })
}
