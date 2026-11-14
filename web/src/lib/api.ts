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

export async function api<T>(method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
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

/**
 * A write that answers with a file (a PDF) instead of JSON: the same CSRF handling as `api`,
 * and the response headers, which carry what the file holds.
 */
export async function apiFile(path: string, body: unknown = {}): Promise<{ blob: Blob; headers: Headers }> {
  const send = async () =>
    fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': await ensureCsrf() },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    })
  let res = await send()
  if (res.status === 403 && (await res.clone().text()).includes(CSRF_ERROR)) {
    csrf = null
    res = await send()
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      msg = ((await res.json()) as { error?: string }).error ?? msg
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg)
  }
  return { blob: await res.blob(), headers: res.headers }
}

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
/** How winners get their product: they pick in chat, or the listing is the product. */
export type SaleMode = 'winners_pick' | 'listing'
export interface Organization {
  id: number
  name: string
  slug: string
  hostUsername: string | null
  /** How the shop usually sells. A new show starts this way; a show can differ. */
  saleMode?: SaleMode
  /** Whether a winner's shelf spot names the product by itself ten minutes after the show ends. */
  autoPick?: boolean
  /** Whatnot names, besides the host, whose chat lines may set an offer on a lot ("#45 bogo"). */
  trustedMods?: string[]
  /** False when the shop's shows need no laptop: one product per listing, and Whatnot's orders are applied as they arrive. */
  laptopNeeded?: boolean
}
/** What the pick worker found for a win still waiting for its product, once the show was over. */
export interface PickFound {
  state: 'product' | 'none' | 'unknown_code' | 'several' | 'unread' | 'no_layout'
  spot?: string
  spots?: string[]
  skuId?: number
  name?: string
  /** A win that may hold several products ("two for one"): the winner's other picks, and how many it may hold. */
  extra?: { spot: string; skuId: number; name: string }[]
  allowed?: number
  /** Why a product the winner picked was not assigned by itself. */
  held?: ('off' | 'unconfirmed' | 'clock' | 'doubt' | 'near' | 'unclear' | 'short')[]
}
/** The licence in force and whether to ask this person to accept it (only the shop's owner). */
export interface LicenceStatus {
  version: string
  needed: boolean
}
export interface Me {
  user: User
  organization: Organization
  permissions: string[]
  licence?: LicenceStatus
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
  /** The show's title on Whatnot, once the recording is matched to it. */
  title?: string | null
  /** false: no laptop recorded it; the app made the show from Whatnot's own record. */
  recorded?: boolean
  /** How this show sells, and whether that was chosen for it (false: the shop's usual way). */
  saleMode?: SaleMode
  saleModeSet?: boolean
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
  /** What Whatnot offers for this product, when a listing there is linked to it. `onWhatnot` is null when Whatnot gave no count. */
  whatnot?: { listings: number; onWhatnot: number | null; differs: boolean } | null
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
  /** Every product of the sale with its part of the price, where the server sent them (the
   * orders pages). One entry for almost every sale; more for a "two for one". */
  products?: SaleProduct[] | null
  /** How many products a person allowed on this win (null: one, or the show's offers). */
  productsAllowed?: number | null
  /** A win waiting for its product: the Whatnot listing its order was for, when the app holds it. */
  listing?: { title: string; options: string; linked: boolean; off: boolean } | null
  item: string
  /** The listing title without Whatnot's lot number ("$1 BEAUTY PULLS" for "… #520"). */
  itemTitle?: string
  /** Whatnot's lot number from the listing title, when it has one. */
  lot?: number | null
  /** False when the title names one lot of one show ("… #520", "CHOOSE ANY…"): the server
   * never learns it as a product's name, so "remember this name" is not offered. */
  rememberName?: boolean
  /** How the sale's show sells. In a winner's-pick show the product a person chooses is that
   * winner's pick, so "remember this name" starts unticked. */
  saleMode?: SaleMode
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
  /** Where the win was read from: the chat, Whatnot's video player, or (no win line at all) Whatnot's own order. */
  source: 'chat' | 'player' | 'whatnot' | null
  /** When the win happened (the winning line's capture time). */
  wonAt: string | null
  /** The decision waiting on this sale in Review, when there is one (orders routes only). */
  review?: { id: number; reason: string; pick?: PickFound['state'] | null } | null
  /** What the winner said around the win; only on a show's sales list. */
  chat?: SaleChatLine[]
  createdAt: string
}
export interface SaleProduct {
  /** The row to change or take off; null for a sale read as its own one product. */
  rowId: number | null
  quantity: number
  shareCents: number | null
  sku: { id: number; code: string; name: string; productName: string }
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
  /** The shelf spot the winner typed in chat ("B4"), on a show with shelves laid out. */
  shelf?: { spot: string; skuId: number; name: string } | null
  createdAt: string
}
/** One spot of a show's shelf layout ("B4" = shelf B, spot 4) and the product on it. */
export interface Shelf {
  spot: string
  skuId: number
  code: string
  name: string
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
/** Where a shipped order's parcel is, as Whatnot last said (labels.ts has the words). */
export type ParcelState = 'waiting' | 'late' | 'moving' | 'delivered' | 'failed' | 'other'
export interface OrderParcel {
  /** One word for the order: every parcel of the buyer in that show taken together. */
  state: ParcelState
  changedAt: string
  parcels: { status: string; carrier: string | null; tracking: string | null; changedAt: string; url: string | null }[]
}
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
  /** Shipped orders only; null when Whatnot lists no parcel for the buyer, or the shop is not connected. */
  parcel?: OrderParcel | null
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
  /** 'ready': only the buyers whose order is Ready to pick; null: everyone in the show. */
  only: 'ready' | null
  /** Buyers of the show the filter left out (in progress, shipped, in Review, gift only, or a slip that still needs a person). */
  leftOut: number
  /** How many of the show's slips are ready to pick: the order is, and nothing on the slip waits for a person. */
  ready?: number
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
  /** The shop is connected to Whatnot: the app reads the show's parcels itself. */
  parcelsFromWhatnot?: boolean
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
  action: 'mark_paid' | 'cancel' | 'decline' | 'unchanged' | 'unmatched' | 'skip' | 'record' | 'recorded' | 'reopen'
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
  /** The Whatnot connection needs a person (rejected token, missing permission). */
  whatnotProblem?: boolean
}

// ---------- Whatnot Seller API connection ----------
/** Why calls to Whatnot are failing, as a code the page turns into words (labels.ts). */
export type WhatnotProblem = 'token_rejected' | 'no_permission' | 'unreachable'
export interface WhatnotStatus {
  /** The server has an access token at all (the developer sets it; it is never shown). */
  configured: boolean
  connected: boolean
  username: string | null
  connectedAt: string | null
  lastCheckedAt: string | null
  problem: WhatnotProblem | null
  /** 'shadow': orders are compared and a person applies them; 'auto': applied as they arrive. */
  ordersMode: 'shadow' | 'auto'
  webhooks: { configured: boolean; url: string | null; lastEventAt: string | null; waiting?: number }
  sync: { resource: string; lastOkAt: string | null; failing: boolean }[]
}
export interface Overview {
  shows: ShowTiles[]
  totals: Record<string, number>
  topBuyers: TopBuyer[]
  topSkus: TopSku[]
  lowStock: LowStockItem[]
  attention: Attention
}

/** A show as Whatnot has it. `id` is this app's id for it. */
export interface WhatnotShowInfo {
  id: number
  title: string
  status: string
  startsAt: string | null
  endedAt: string | null
  url: string | null
  ordersReadAt: string | null
}
/** Whatnot's orders for a show next to the app's sales (GET /api/shows/:id/whatnot). */
export interface WhatnotCompare {
  connected: boolean
  mode?: 'shadow' | 'auto'
  /** Who matched the recording to the Whatnot show: by time, a person, or made from Whatnot's record. */
  link?: 'auto' | 'manual' | 'made' | null
  show?: WhatnotShowInfo | null
  choices?: WhatnotShowInfo[]
  /** The show ended and every order was read since: only then is a sale with no order called out. */
  complete?: boolean
  info?: { orders: number; giveaways: number; noBuyer: number } | null
  summary?: Record<string, number>
  rows?: ReconcileRow[]
  uncovered?: { saleId: number; buyer: string; item: string; quantity: number; priceCents: number | null; status: string }[]
  queued?: boolean
  applied?: Record<string, number>
}

// ---------- Shipping: one package and one label per buyer (GET /api/shipping/shows/:id) ----------
/** Where a buyer's shipping stands; the page turns the code into words (labels.ts). */
export type ShippingState = 'none' | 'wait' | 'shared' | 'reading' | 'ready' | 'second' | 'split' | 'hold' | 'pickup' | 'labelled' | 'shipped' | 'failed' | 'other'
export interface ShippingPackage {
  id: number
  status: string
  /** Items of this show in the package, and items of another show bundled with them. */
  items: number
  otherItems: number
  /** Those other items belong to a show that is live now: the bundle is still filling. */
  liveElsewhere: boolean
  tracking: string | null
  carrier: string | null
  labelReady: boolean
  dueAt: string | null
  /** What Whatnot wants done before it ships (codes; labels.ts has the words). */
  holds: string[]
  pickup: boolean
  /** The package in Seller Hub. */
  url: string | null
  labelRequestedAt: string | null
  labelProblem: string | null
  /** "Make labels" would ask for this package's label right now (the server decides). */
  askable: boolean
  /** When its label was last opened to print from here; "Print labels" skips it then. */
  printedAt?: string | null
}
export interface ShippingBuyer {
  buyer: string | null
  items: number
  /** Items not in any package on Whatnot yet. */
  unplaced: number
  packages: ShippingPackage[]
  state: ShippingState
  orderId: number | null
  orderStatus: OrderStatus | null
}
export interface Shipping {
  showId: number
  showDate?: string
  connected: boolean
  matched: boolean
  mode?: 'shadow' | 'auto'
  /** complete: the show ended and all its orders and packages were read after that. */
  show?: { title: string; status: string; ended: boolean; complete: boolean }
  buyers: ShippingBuyer[]
  totals: Partial<Record<ShippingState | 'buyers', number>>
  /** How many labels "Make labels" would ask Whatnot for right now. */
  labelsToMake?: number
  /** Labels that exist and have not been printed from here; and the ones that have. */
  labelsToPrint?: number
  labelsPrinted?: number
  /** Orders the carrier already has that the app still shows as waiting. */
  toMarkShipped?: number
  queued?: boolean
  requested?: number
  marked?: number
}

// ---------- Whatnot's own numbers: a show's results and the financial ledger ----------
/** Whatnot's ledger for a show or a span of days, as Whatnot states it (cents, signed). */
export interface WhatnotFigures {
  /** The shop's currency: the only one summed. Lines in any other are counted, not added. */
  currency?: string
  otherCurrencyLines?: number
  /** The sum of Whatnot's own ledger lines. */
  netCents: number
  kinds: { kind: string; lines: number; cents: number }[]
  /** The fees Whatnot lists on order earnings (codes; labels.ts has the words). */
  fees: Record<string, number>
  itemPricesCents: number
  costOfGoodsCents: number | null
  through: string | null
}
/** GET /api/dashboard/shows/:id → `whatnot` (null for those who may not read reports). */
export interface WhatnotRecap {
  title: string
  /** The access token may not read reports, so Whatnot will not give the show's results. */
  resultsNotAllowed?: boolean
  /** GENERATED | PENDING | NOT_ENDED | WILL_NOT_BE_CALCULATED | null (never asked) */
  resultsStatus: string | null
  resultsAt: string | null
  results: Record<string, number> | null
  ledger: WhatnotFigures | null
}
/** GET /api/whatnot/ledger */
export interface WhatnotLedger {
  connected: boolean
  lines?: number
  building?: boolean
  lastImportedAt?: string | null
  /** The access token may not read reports. */
  notAllowed?: boolean
  lastFailed?: boolean
  unreadable?: boolean
  /** Lines of the latest report that are not in the figures: unreadable, or repeating a transaction id. */
  skippedLines?: number
  repeatedLines?: number
  days?: number
  recent?: WhatnotFigures | null
  shows?: { showId: number | null; title: string; startedAt: string | null; netCents: number; lines: number }[]
}

/** What the team says about a buyer: someone to look after, or someone to keep an eye on. */
export type BuyerFlag = 'vip' | 'watch'
/** What a buyer bought, from the shop's sales. A "show" is a show date. */
export interface BuyerFigures {
  /** Items that still stand (not cancelled, payment not failed). */
  items: number
  /** What was paid for. */
  paidCents: number
  shows: number
  firstShow: string | null
  lastShow: string | null
  cancelled: number
  failed: number
}
export interface BuyerRow extends BuyerFigures {
  username: string
  /** Names they have also bought under. */
  names: string[]
  flag: BuyerFlag | null
  hasNote: boolean
}
export interface BuyerList {
  buyers: BuyerRow[]
  total: number
  counts: { all: number; vip: number; watch: number; noted: number }
  limit: number
  offset: number
}
export interface BuyerProblem {
  saleId: number
  showId: number
  showDate: string
  item: string
  quantity: number
  priceCents: number | null
  status: SaleStatus
}
export interface BuyerProfile extends BuyerFigures {
  username: string
  names: string[]
  /** False for a name nobody has won under or said anything about. */
  known: boolean
  /** Whatnot named the account: a new username lands on this page by itself. */
  followed: boolean
  flag: BuyerFlag | null
  note: string | null
  notedBy: string | null
  notedAt: string | null
  gifts: number
  problems: BuyerProblem[]
}
/** lower-case name → the team's flag and whether there is a note, for every flagged or noted buyer. */
export type BuyerFlags = Record<string, { flag: BuyerFlag | null; note: boolean }>

/** One listing in the shop's Whatnot catalog, next to the product here it is linked to. */
export interface WhatnotListing {
  id: number
  title: string
  /** "Red / L" */
  options: string
  skuCode: string | null
  priceCents: number | null
  /** Whatnot's own count, as last read. */
  onWhatnot: number | null
  url: string | null
  /** Who decided: a matching code, a person, or a person's "none of ours" ('off'). */
  link: 'code' | 'person' | 'off' | null
  /** `canSell` is what the shop can still sell of the product: never below zero. */
  sku: { id: number; code: string; name: string; canSell: number } | null
  /** How many listings the linked product has on Whatnot, and what they offer together (null when Whatnot gave no count). */
  listings: number
  offered: number | null
  /** Everything Whatnot offers for the product is not what the shop can sell. */
  differs: boolean
}
export interface WhatnotCatalog {
  connected: boolean
  readAt: string | null
  /** The access token may not read the catalog. */
  notAllowed: boolean
  listings: WhatnotListing[]
  counts: { all: number; linked: number; undecided: number; differs: number }
}

/** A giveaway Whatnot lists for a show, and the product a person said it was (null: waiting). */
export interface Giveaway {
  id: number
  buyer: string
  title: string
  quantity: number
  showId: number
  showDate: string
  wonAt: string | null
  /** Whatnot cancelled it: nothing ships, and a product still on it should be taken off. */
  cancelled: boolean
  product: { id: number; code: string; name: string; productName: string } | null
}
