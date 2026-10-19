// Chat Scraper content script — capture runs here; storage lives in the background worker.
import type { Broadcast } from '../shared/messages'
import { Controller } from './controller'

declare global {
  interface Window {
    __csExt?: boolean
  }
}

if (!window.__csExt) {
  window.__csExt = true
  const controller = new Controller()
  chrome.runtime.onMessage.addListener((msg: Broadcast) => {
    if (msg.cmd === 'toggle') controller.toggle()
  })
  void controller.init()
}
