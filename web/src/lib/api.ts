export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message)
  }
}

const CSRF_COOKIE = 'csrf'

function readCookie(name: string): string | null {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]!) : null
}

let csrfReady: Promise<void> | null = null
/** The API sets a double-submit cookie; every write echoes it in a header. */
async function ensureCsrf(): Promise<string> {
  if (!readCookie(CSRF_COOKIE)) {
    csrfReady ??= fetch('/api/auth/csrf', { credentials: 'same-origin' }).then(() => undefined)
    await csrfReady
  }
  return readCookie(CSRF_COOKIE) ?? ''
}

export async function api<T>(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = await ensureCsrf()
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let json: unknown = undefined
  try {
    json = text ? JSON.parse(text) : undefined
  } catch {
    /* not JSON */
  }
  if (!res.ok) {
    const msg = (json as { error?: string } | undefined)?.error ?? `${res.status} ${res.statusText}`
    throw new ApiError(res.status, msg, json)
  }
  return json as T
}

export const apiGet = <T>(path: string) => api<T>('GET', path)

export type Health = { status: 'ok' | 'degraded'; db: boolean }
export const getHealth = () => apiGet<Health>('/api/health')

// ---------- types mirrored from api/app/serializers.py ----------
export type Role = 'owner' | 'manager' | 'fulfillment' | 'viewer'
export interface User {
  id: number
  email: string
  name: string
  role: Role
  disabled: boolean
  createdAt: string
  lastLoginAt: string | null
}
export interface Organization {
  id: number
  name: string
  slug: string
}
export interface Me {
  user: User
  organization: Organization
  permissions: string[]
}
export interface Invite {
  id: number
  email: string
  role: Role
  invitedBy: string | null
  expiresAt: string
  pending: boolean
}
export interface Device {
  id: number
  name: string
  keyPrefix: string
  createdAt: string
  lastSeenAt: string | null
  revoked: boolean
}
export interface AuditEntry {
  id: number
  at: string
  actorType: string
  actor: string
  action: string
  targetType: string | null
  targetId: string | null
  details: Record<string, unknown> | null
}
export interface Show {
  id: number
  key: string
  date: string
  startedAt: string
  updatedAt: string
  sourceUrl: string | null
  messageCount: number
}
export interface ChatMessage {
  id: number
  seq: number
  username: string
  body: string
  rawText: string
  capturedAt: string
}
