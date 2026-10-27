import { isExtensionPage, showOpenedAsFile } from '../shared/openedAsFile'
import { forgetConnection, loadSettings, saveConnection, saveServerUrl, serverOrigin } from '../upload/settings'
import { UploadError, type PingReply, pairDevice, ping, revokeDevice } from '../upload/uploader'
import { extensionVersion, isNewer } from '../upload/version'

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
      parked?: number
      lastError?: string
      lastSentAt?: number
    }
    // Built from text nodes: lastError can carry text that came from the server or from chat.
    const el = $('stats')
    const b = (n: number) => Object.assign(document.createElement('b'), { textContent: String(n) })
    el.replaceChildren('Uploaded ', b(s.uploaded), ' · pending ', b(s.pending))
    if (s.parked) el.append(' · set aside ', b(s.parked))
    if (s.lastSentAt) el.append(` · last upload ${new Date(s.lastSentAt).toLocaleTimeString()}`)
    if (s.lastError) el.append(' · ', Object.assign(document.createElement('span'), { textContent: s.lastError, style: 'color:#FF8A80' }))
  } catch {
    $('stats').textContent = ''
  }
}

/** The web app serves a newer build than this one: say where to update it. */
function showUpdate(r: Pick<PingReply, 'latestExtension'>): void {
  const behind = isNewer(r.latestExtension, extensionVersion())
  $('update-card').hidden = !behind
  if (behind) $('latest').textContent = r.latestExtension!
}

const changed = () => chrome.runtime.sendMessage({ cmd: 'settingsChanged' }).catch(() => {})

/** Validate and save the address field; says why when it is not acceptable. */
async function serverFromField(): Promise<string | null> {
  try {
    const origin = await saveServerUrl(url.value)
    if (!origin) {
      say('Enter the web app address first.', 'bad')
      return null
    }
    url.value = origin
    return origin
  } catch (err) {
    say((err as Error).message, 'bad')
    return null
  }
}

/** Store the key for this server; if it belongs to a different shop than before, set unsent lines aside. */
async function connectWith(origin: string, apiKey: string, organization?: string): Promise<string> {
  const otherShop = await saveConnection({ apiKey, origin, organization })
  let note = ''
  if (otherShop) {
    const r = (await chrome.runtime.sendMessage({ cmd: 'parkPending' })) as { parked?: number }
    if (r?.parked) note = ` ${r.parked} unsent lines from the previous shop were set aside and will not be sent here.`
  }
  void changed()
  return note
}

async function init(): Promise<void> {
  const s = await loadSettings()
  url.value = s.serverUrl
  key.value = ''
  showConnected(!!s.apiKey)
  if (s.apiKey && s.serverUrl) {
    try {
      const r = await ping(s)
      showConnected(true, `Connected as "${r.device}" (${r.organization})`)
      showUpdate(r)
    } catch (err) {
      showConnected(true, 'Key saved, but not connected')
      say(explain(err, 'key'), 'bad')
    }
  }
  await refreshStats()
}

async function pair(): Promise<void> {
  if (code.value.replace(/[^a-z0-9]/gi, '').length !== 8) return say('Enter the 8-character code from the web app.', 'bad')
  const server = await serverFromField()
  if (!server) return
  say('Connecting…')
  try {
    const r = await pairDevice(server, code.value)
    const note = await connectWith(server, r.key, r.organization)
    key.value = ''
    code.value = ''
    showConnected(true, `Connected as "${r.device}" (${r.organization})`)
    say(`Connected as "${r.device}" (${r.organization}).${note}`, 'good')
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
    showUpdate(r)
  } catch (err) {
    say(explain(err, 'key'), 'bad')
  }
}

$('disconnect').onclick = async () => {
  if (!confirm('Disconnect this laptop? Its key stops working on the server. Captured lines stay saved here.')) return
  const s = await loadSettings()
  const revoked = s.apiKey ? await revokeDevice(s) : false
  await forgetConnection()
  key.value = ''
  showConnected(false)
  void changed()
  say(revoked ? 'Disconnected; the key no longer works.' : 'Disconnected on this laptop. Remove it in the web app too (Settings → Laptops).')
}

// Advanced: paste a device key made in the web app instead of using a code.
$('test-key').onclick = async () => {
  say('Testing…')
  let origin: string
  try {
    origin = serverOrigin(url.value)
  } catch (err) {
    return say((err as Error).message, 'bad')
  }
  try {
    const r = await ping({ serverUrl: origin, apiKey: key.value.trim() })
    say(`Connected as "${r.device}" (${r.organization}).`, 'good')
  } catch (err) {
    say(explain(err, 'key'), 'bad')
  }
}

$('save').onclick = async () => {
  const origin = await serverFromField()
  if (!origin) return
  const k = key.value.trim()
  if (!k) return say('Paste a device key first, or connect with a code.', 'bad')
  let org: string | undefined
  try {
    org = (await ping({ serverUrl: origin, apiKey: k })).organization
  } catch {
    /* saved anyway: uploads say what is wrong until the key is fixed */
  }
  const note = await connectWith(origin, k, org)
  showConnected(true, org ? `Connected (${org})` : 'Key saved, but not connected')
  say('Saved.' + note, 'good')
  setTimeout(refreshStats, 1500)
}

$('flush').onclick = async () => {
  say('Uploading…')
  const r = (await chrome.runtime.sendMessage({ cmd: 'flushUploads' })) as { status: string; sent?: number; detail?: string }
  say(r.status === 'sent' ? `Sent ${r.sent}.` : r.status === 'idle' ? 'Nothing pending.' : `${r.status}${r.detail ? ': ' + r.detail : ''}`,
    r.status === 'sent' || r.status === 'idle' ? 'good' : 'bad')
  await refreshStats()
}

if (isExtensionPage()) {
  $('version').textContent = 'v' + extensionVersion()
  setInterval(refreshStats, 5000)
  void init()
} else {
  showOpenedAsFile('Settings', 'connect this laptop')
}
