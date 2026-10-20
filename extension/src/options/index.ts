import { loadSettings, normaliseUrl, saveSettings } from '../upload/settings'
import { UploadError, pairDevice, ping } from '../upload/uploader'

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T
const url = $<HTMLInputElement>('url')
const key = $<HTMLInputElement>('key')
const code = $<HTMLInputElement>('code')
const msg = $('msg')

function say(text: string, kind: 'good' | 'bad' | '' = ''): void {
  msg.textContent = text
  msg.className = 'msg ' + kind
}

/** Pairing card when this laptop has no key; connected card once it has one. */
function showConnected(connected: boolean, who = ''): void {
  $('pair-card').hidden = connected
  $('connected-card').hidden = !connected
  if (connected) $('who').textContent = who || 'Connected'
}

function explain(err: unknown, what: 'code' | 'key'): string {
  const e = err as UploadError
  if (e.kind === 'auth') return 'The server rejected this key.'
  if (e.kind === 'rejected') return what === 'code' ? `That code did not work: ${e.message}.` : e.message
  return 'Could not reach the web app: ' + e.message
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

const flush = () => chrome.runtime.sendMessage({ cmd: 'flushUploads' }).catch(() => {})

async function init(): Promise<void> {
  const s = await loadSettings()
  url.value = s.serverUrl
  key.value = s.apiKey
  showConnected(!!s.apiKey)
  if (s.apiKey && s.serverUrl) {
    try {
      const r = await ping(s)
      showConnected(true, `Connected as "${r.device}" (${r.organization})`)
    } catch (err) {
      showConnected(true, 'Key saved, but not connected')
      say(explain(err, 'key'), 'bad')
    }
  }
  await refreshStats()
}

async function pair(): Promise<void> {
  const server = normaliseUrl(url.value)
  if (!server) return say('Enter the web app address first.', 'bad')
  if (code.value.replace(/[^a-z0-9]/gi, '').length !== 8) return say('Enter the 8-character code from the web app.', 'bad')
  say('Connecting…')
  try {
    const r = await pairDevice(server, code.value)
    await saveSettings({ serverUrl: server, apiKey: r.key })
    key.value = r.key
    code.value = ''
    showConnected(true, `Connected as "${r.device}" (${r.organization})`)
    say(`Connected as "${r.device}" (${r.organization}).`, 'good')
    void flush()
    setTimeout(refreshStats, 1500)
  } catch (err) {
    say(explain(err, 'code'), 'bad')
  }
}

$('pair').onclick = () => void pair()
code.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void pair()
})

$('test').onclick = async () => {
  say('Testing…')
  try {
    const r = await ping(await loadSettings())
    say(`Connected as "${r.device}" (${r.organization}).`, 'good')
  } catch (err) {
    say(explain(err, 'key'), 'bad')
  }
}

$('disconnect').onclick = async () => {
  if (!confirm('Disconnect this laptop? Captured lines stay saved here until you connect again.')) return
  await saveSettings({ serverUrl: url.value, apiKey: '' })
  key.value = ''
  showConnected(false)
  say('Disconnected. Captured lines stay on this laptop until you connect again.')
}

// Advanced: paste a device key made in the web app instead of using a code.
$('test-key').onclick = async () => {
  say('Testing…')
  try {
    const r = await ping({ serverUrl: normaliseUrl(url.value), apiKey: key.value.trim() })
    say(`Connected as "${r.device}" (${r.organization}).`, 'good')
  } catch (err) {
    say(explain(err, 'key'), 'bad')
  }
}

$('save').onclick = async () => {
  await saveSettings({ serverUrl: url.value, apiKey: key.value })
  showConnected(!!key.value.trim())
  say('Saved.', 'good')
  void flush()
  setTimeout(refreshStats, 1500)
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
