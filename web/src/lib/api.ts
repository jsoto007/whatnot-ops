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
  hostUsername: string | null
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

// ---------- inventory (Phase 4) ----------
export interface Alias {
  id: number
  alias: string
}
export interface Sku {
  id: number
  productId: number
  code: string
  name: string
  priceCents: number | null
  onHand: number
  reserved: number
  available: number
  lowStockAt: number
  low: boolean
  active: boolean
  aliases: Alias[]
}
export interface Product {
  id: number
  name: string
  notes: string | null
  active: boolean
  skus: Sku[]
}
export interface Movement {
  id: number
  kind: 'restock' | 'adjust' | 'reserve' | 'release' | 'commit'
  quantity: number
  sourceType: string
  sourceId: string
  reason: string | null
  actor: string
  onHandAfter: number
  reservedAfter: number
  at: string
}
export interface ImportRow {
  item: string
  quantity: number
  skuCode: string | null
  action: 'create' | 'set' | 'unchanged' | 'skip'
  before: number | null
  error: string | null
}

export const money = (cents: number | null | undefined) =>
  cents == null ? '—' : (cents / 100).toLocaleString(undefined, { style: 'currency', currency: 'USD' })

// ---------- parser / sales / review (Phase 5) ----------
export type EventKind = 'sale' | 'claim' | 'gift' | 'cancel' | 'payment_failed' | 'no_pick' | 'listing' | 'chatter'
export type SaleStatus = 'detected' | 'reserved' | 'paid' | 'cancelled' | 'payment_failed' | 'fulfilled'
export interface ParsedEvent {
  id: number
  messageId: number
  seq: number
  kind: EventKind
  buyer: string
  item: string
  quantity: number
  priceCents: number | null
  auctionType: string
  rule: string
  confidence: number
  rawText: string
  capturedAt: string
}
export interface Sale {
  id: number
  showId: number
  showDate: string
  buyer: string
  sku: { id: number; code: string; name: string; productName: string } | null
  item: string
  quantity: number
  priceCents: number | null
  auctionType: string
  status: SaleStatus
  statusReason: string | null
  flagged: boolean
  orderId: number | null
  externalOrderId: string | null
  paidAt: string | null
  seq: number | null
  rawText: string | null
  createdAt: string
}
export interface ReviewItem {
  id: number
  showId: number | null
  showDate: string | null
  reason: string
  summary: string
  source: string
  status: 'open' | 'resolved' | 'dismissed'
  proposal: Record<string, unknown> | null
  message: { id: number; seq: number; username: string; body: string; rawText: string; capturedAt: string } | null
  context: { seq: number; username: string; body: string }[]
  sale: Sale | null
  createdAt: string
}
export interface ParseStats {
  parsed: number
  events: Record<string, number>
  sales: Record<string, number>
  openReviews: number
  job: { id: number; status: string; error: string | null } | null
}

// ---------- AI supervisor (Phase 6) ----------
export interface AiStatus {
  provider: string
  enabled: boolean
  callsToday: number
  failedToday: number
  lastError: string | null
  openProposals: number
}

// ---------- orders / fulfilment (Phase 7) ----------
export type OrderStatus = 'open' | 'paid' | 'picking' | 'packed' | 'shipped' | 'cancelled'
export interface Order {
  id: number
  showId: number
  showDate: string
  buyer: string
  status: OrderStatus
  verified: boolean
  items: number
  totalCents: number
  notes: string | null
  tracking: string | null
  pickedAt: string | null
  packedAt: string | null
  shippedAt: string | null
  createdAt: string
  sales: Sale[] | null
}
export interface PickItem {
  skuId: number
  code: string
  product: string
  variant: string
  quantity: number
  orders: number
}
export interface ReconcileRow {
  buyer: string
  item: string
  quantity: number
  priceCents: number | null
  status: string
  externalId: string | null
  action: 'mark_paid' | 'cancel' | 'unchanged' | 'unmatched' | 'skip'
  saleId: number | null
  note: string | null
}

// ---------- dashboard / reports (Phases 8–9) ----------
export interface ShowTiles {
  showId: number
  date: string
  startedAt: string
  updatedAt: string
  live: boolean
  messages: number
  itemsSold: number
  buyers: number
  red: number
  bundles: number
  ultra: number
  mega: number
  gifts: number
  noPicks: number
  cancelled: number
  revenuePaidCents: number
  revenuePendingCents: number
  orders: Record<string, number>
  toFulfil: number
  openReviews: number
}
export interface TopBuyer {
  buyer: string
  items: number
  paidCents: number
  shows: number
}
export interface TopSku {
  skuId: number
  code: string
  product: string
  variant: string
  sold: number
  paidCents: number
  available: number
  sellThrough: number | null
}
export interface Attention {
  openReviews: number
  toFulfil: number
  lowStock: number
  unreconciledShows: { showId: number; date: string; pendingCents: number; openSales: number }[]
  live: ShowTiles | null
}
export interface Overview {
  shows: ShowTiles[]
  totals: Record<string, number>
  topBuyers: TopBuyer[]
  topSkus: TopSku[]
  lowStock: { skuId: number; code: string; product: string; available: number }[]
  attention: Attention
}
