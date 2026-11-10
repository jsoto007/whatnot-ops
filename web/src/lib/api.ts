export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message)
  }
}

const CSRF_ERROR = 'missing or invalid CSRF token'

let csrf: Promise<string> | null = null
/** The server keeps this page's CSRF token in its signed session cookie and hands it out
 *  once (`/api/auth/csrf`); every write echoes it in a header. Kept in memory only. */
function ensureCsrf(): Promise<string> {
  csrf ??= fetch('/api/auth/csrf', { credentials: 'same-origin' })
    .then((r) => r.json() as Promise<{ token?: string }>)
    .then((j) => j.token ?? '')
    .catch((e: unknown) => {
      csrf = null
      throw e
    })
  return csrf
}

export async function api<T>(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const send = async () => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (method !== 'GET') headers['X-CSRF-Token'] = await ensureCsrf()
    return fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }
  let res = await send()
  if (res.status === 403 && method !== 'GET' && (await res.clone().text()).includes(CSRF_ERROR)) {
    // The session cookie expired or was replaced since the token was fetched: once more.
    csrf = null
    res = await send()
  }
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
  /** The shop's admin (ADMIN_EMAIL) or the support account (IT_SUPPORT): always an owner, cannot be demoted or disabled. */
  isAdmin?: boolean
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
  /** Chat Scraper version it last called in with; null before 1.5.0 or before its first call */
  extensionVersion: string | null
  /** Who made its key (the key stops when they lose access); null when that was not recorded */
  createdBy: string | null
  /** Its maker is disabled or may no longer manage laptops, yet the key (made before that was enforced) still works unless revoked */
  makerLostAccess: boolean
}
/** One activity-log line, already in words: the server never sends action codes or raw details. */
export interface AuditEntry {
  id: number
  at: string
  who: string
  summary: string
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
  /** One unit's weight in ounces (to 0.01 oz); null until someone weighs it. */
  weightOz: number | null
  onHand: number
  reserved: number
  available: number
  /** How many to buy to fill sold orders; >0 only when oversold (available < 0). */
  short: number
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
/** A ledger row as the Inventory page reads it: which item, and for sales which buyer and show. */
export interface Activity extends Movement {
  skuId: number
  skuCode: string
  productName: string
  sale: { buyer: string; showId: number; showDate: string } | null
}
/** /api/inventory row: the SKU plus what the table needs to decide what to do next. */
export interface StockLevel extends Sku {
  productName: string
  sold: number
  lastReceivedAt: string | null
  lastCountedAt: string | null
}
export interface ReceiveResult {
  receiptId: string
  alreadyApplied: boolean
  units: number
  lines: { skuId: number; code: string; product: string; quantity: number; onHand: number; created: boolean }[]
}
export interface CountResult {
  countId: string
  changed: number
  unchanged: number
  net: number
  lines: { skuId: number; code: string; before: number; after: number; delta: number }[]
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
  /** The listing title without Whatnot's lot number ("$1 BEAUTY PULLS" for "… #520"). */
  itemTitle?: string
  /** Whatnot's lot number from the listing title, when it has one. */
  lot?: number | null
  quantity: number
  priceCents: number | null
  auctionType: string
  status: SaleStatus
  statusReason: string | null
  flagged: boolean
  /** Free gifts packed with this lot ("Gift 2" on the order slips); set by a person. */
  giftCount: number
  orderId: number | null
  externalOrderId: string | null
  paidAt: string | null
  seq: number | null
  rawText: string | null
  /** "player" when the win was read from Whatnot's video player, "chat" for a chat line. */
  source: 'chat' | 'player' | null
  /** When the win happened (the winning line's capture time). */
  wonAt: string | null
  /** The decision waiting on this sale in Review, when there is one (orders routes only). */
  review?: { id: number; reason: string } | null
  /** What the winner said around the win; only on a show's sales list. */
  chat?: SaleChatLine[]
  createdAt: string
}
export interface SaleChatLine {
  seq: number
  username: string
  body: string
  capturedAt: string
  role: 'buyer' | 'host' | 'platform' | 'other'
  /** Said after the win (else in the two minutes before it). */
  after: boolean
  /** Names the lot by its number ("#73"), which is how it was tied to this win. */
  namesLot: boolean
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
  job: { id: number; status: string } | null
}

// ---------- AI supervisor (Phase 6) ----------
export interface AiStatus {
  provider: string
  enabled: boolean
  callsToday: number
  failedToday: number
  /** When the last call failed; the vendor's error text stays on the server. */
  lastFailedAt: string | null
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
  /** The packer's name or initials ("Packed by" on the paper slip); set on the first move to packed. */
  packedBy: string | null
  shippedAt: string | null
  createdAt: string
  sales: Sale[] | null
  /** Gift lines the host gave this buyer in the show (detail view only; gifts are not sales). */
  gifts: { item: string; seq: number | null }[] | null
}
/** One row of a buyer's order slip: a unit to pack, or a giveaway ("Gift"). */
export interface SlipRow {
  kind: 'sale' | 'gift'
  saleId: number | null
  /** "Quick Hit 2", "Ultra 19", "Listing", "Gift". */
  auction: string
  /** The product to pick, or "No Pick" when nobody has said which it is yet. */
  product: string
  matched: boolean
  giftCount: number
  /** The lot's price, on its first row only. */
  priceCents: number | null
  /** A further unit of the lot above: the sheet shows "-" for its price. */
  continued: boolean
  /** Whatnot's order number for the lot, when the row comes from Whatnot's Shipments read. */
  whatnotOrder: string | null
  /** False for a Whatnot lot with no win recorded in the app. */
  inApp: boolean
  /** False for an app sale Whatnot's Shipments read does not list. */
  onWhatnot: boolean
  /**
   * For a "No Pick" row: what the winner (or the host, about them) said in chat after the win,
   * oldest first. With `saidMore` > 0 it is the first line, then the last ones: that many were
   * left out after the first.
   */
  said: string[]
  saidMore: number
}
export interface BuyerSlip {
  username: string
  orderId: number | null
  orderStatus: OrderStatus | null
  needsReview: boolean
  items: number
  gifts: number
  rows: SlipRow[]
  /** This buyer on Whatnot's Shipments list; null when not read yet or not listed there. */
  whatnot: { items: number | null; statuses: string[] } | null
}
export interface OrderSlips {
  showId: number
  showDate: string
  totals: { buyers: number; items: number; gifts: number }
  summary: { username: string; items: number; gifts: number }[]
  slips: BuyerSlip[]
  /** Whatnot's Shipments list as the scraper read it; null until it has been read. */
  whatnot: {
    readAt: string
    shipments: number
    /** Lots read (0 from an older scraper that sent shipments only). */
    lots: number
    lotsNotInApp: number
    salesNotOnWhatnot: number
    missingHere: string[]
    notOnWhatnot: string[]
  } | null
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
  quickHit: number
  gifts: number
  /** Wins where nothing was picked (the sale names no item). */
  noPicks: number
  /** Auctions that ended without a winner. */
  noWinner: number
  cancelled: number
  revenuePaidCents: number
  revenuePendingCents: number
  orders: Record<string, number>
  toFulfil: number
  openReviews: number
}
export type WalkAwayOutcome = 'no_item' | 'cancelled' | 'payment_failed' | 'unpaid' | 'unconfirmed'
/** A winner with nothing to pack for the show (dashboard.winners_without_purchase). */
export interface WalkAway {
  buyer: string
  outcome: WalkAwayOutcome
  label: string
  item: string
  quantity: number
  seq: number | null
  saleId: number | null
  note: string
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
  short: number
  sellThrough: number | null
}
export interface LowStockItem {
  skuId: number
  code: string
  product: string
  available: number
  short: number
  lowAt: number
  sold: number
}
export interface Attention {
  openReviews: number
  reviewReasons: Record<string, number>
  toFulfil: number
  fulfilItems: number
  fulfilOldestShow: string | null
  lowStock: number
  oversold: LowStockItem[]
  unreconciledShows: { showId: number; date: string; pendingCents: number; openSales: number }[]
  live: (ShowTiles & { pace: number[] }) | null
}
export interface NavCounts {
  openReviews: number
  toFulfil: number
  live: boolean
}
export interface Overview {
  shows: ShowTiles[]
  totals: Record<string, number>
  topBuyers: TopBuyer[]
  topSkus: TopSku[]
  lowStock: LowStockItem[]
  attention: Attention
}
