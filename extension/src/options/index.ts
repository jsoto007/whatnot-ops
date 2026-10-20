import { loadSettings, saveSettings } from '../upload/settings'
import { UploadError, ping } from '../upload/uploader'

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T
const url = $<HTMLInputElement>('url')
const key = $<HTMLInputElement>('key')
const msg = $('msg')

function say(text: string, kind: 'good' | 'bad' | '' = ''): void {
  msg.textContent = text
  msg.className = 'msg ' + kind
}

async function refreshStats(): Promise<void> {
  try {
    const s = (await chrome.runtime.sendMessage({ cmd: 'getUploadStatus' })) as {
      pending: number
      uploaded: number
      lastError?: string
      lastSentAt?: number
    }
    $('stats').innerHTML =
      `Uploaded <b>${s.uploaded}</b> · pending <b>${s.pending}</b>` +
      (s.lastSentAt ? ` · last upload ${new Date(s.lastSentAt).toLocaleTimeString()}` : '') +
      (s.lastError ? ` · <span style="color:#FF8A80">${s.lastError}</span>` : '')
  } catch {
    $('stats').textContent = ''
  }
}

async function init(): Promise<void> {
  const s = await loadSettings()
  url.value = s.serverUrl
  key.value = s.apiKey
  await refreshStats()
}

$('save').onclick = async () => {
  await saveSettings({ serverUrl: url.value, apiKey: key.value })
  say('Saved.', 'good')
  chrome.runtime.sendMessage({ cmd: 'flushUploads' }).catch(() => {})
  setTimeout(refreshStats, 1500)
}

$('test').onclick = async () => {
  say('Testing…')
  try {
    const r = await ping({ serverUrl: url.value.trim().replace(/\/+$/, ''), apiKey: key.value.trim() })
    say(`Connected as "${r.device}" (${r.organization}).`, 'good')
  } catch (err) {
    const e = err as UploadError
    say(e.kind === 'auth' ? 'The server rejected this key.' : 'Could not reach the server: ' + e.message, 'bad')
  }
}

$('flush').onclick = async () => {
  say('Uploading…')
  const r = (await chrome.runtime.sendMessage({ cmd: 'flushUploads' })) as { status: string; sent?: number; detail?: string }
  say(r.status === 'sent' ? `Sent ${r.sent}.` : r.status === 'idle' ? 'Nothing pending.' : `${r.status}${r.detail ? ': ' + r.detail : ''}`,
    r.status === 'sent' || r.status === 'idle' ? 'good' : 'bad')
  await refreshStats()
}

setInterval(refreshStats, 5000)
void init()
