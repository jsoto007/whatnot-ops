// Chat Scraper content script — capture runs here; storage lives in the background worker.
import type { Broadcast, PingReply } from '../shared/messages'
import { Controller } from './controller'

declare global {
  interface Window {
    /** True while a copy of this script that can still reach the extension runs in this world. */
    __csAlive?: () => boolean
  }
}

/** Sent on the page by a newly injected copy; DOM events reach every copy, whatever its world. */
const TAKEOVER = 'chat-scraper:takeover'

// After an extension update the background injects a fresh copy into open Whatnot tabs
// (background.ts reinject()). The old copy is cut off from chrome.* and must go: it
// stops and removes its panel, and the fresh copy carries on the recording.
if (!window.__csAlive?.()) {
  document.dispatchEvent(new CustomEvent(TAKEOVER))
  window.__csAlive = () => !!chrome.runtime?.id
  // Null once retired. The listeners below (and __csAlive, which shares this scope) outlive
  // this copy; holding the Controller would keep the old show's whole log in memory.
  let controller: Controller | null = new Controller()
  const retire = () => {
    // A page script can send this event too, so only a copy that really is cut off obeys it.
    if (chrome.runtime?.id || !controller) return
    controller.destroy()
    controller = null
    document.removeEventListener(TAKEOVER, retire)
  }
  document.addEventListener(TAKEOVER, retire)
  chrome.runtime.onMessage.addListener((msg: Broadcast, _sender, sendResponse) => {
    if (!controller) return
    if (msg.cmd === 'toggle') controller.toggle()
    else if (msg.cmd === 'ping') sendResponse({ recording: controller.capturing } satisfies PingReply)
    else if (msg.cmd === 'released') controller.release(msg.showId)
  })
  void controller.init()
}
