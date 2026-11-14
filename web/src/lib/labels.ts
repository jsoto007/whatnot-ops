import type { OrderStatus, Role, Sale, SaleMode, SaleStatus } from './api'

/**
 * The words the shop reads for the server's codes. A page never prints a raw
 * code (`payment_failed`, `quick_hit`, a role value): it goes through one of
 * these, and an unknown code reads as a neutral word, not as itself.
 */

/** The licence the shop accepts: LICENSE.md at the repo root. The web build serves that file at
 *  the site root, and /license shows it as a page people can read (pages/LicenceTerms.tsx). */
export const LICENCE_TITLE = 'Sales Ops License and Data Terms'
export const LICENCE_URL = '/license'
export const LICENCE_FILE_URL = '/LICENSE.md'
/** The words of the box ticked at sign-up and the banner's button: the same promise in both. */
export const LICENCE_ACCEPT_LEAD = 'I have read and accept the'
export const LICENCE_ACCEPT = `${LICENCE_ACCEPT_LEAD} ${LICENCE_TITLE}`

export const ROLE_LABEL: Record<Role, string> = {
  owner: 'Owner',
  manager: 'Manager',
  fulfillment: 'Fulfillment',
  viewer: 'Viewer',
}
export const roleLabel = (r: string | undefined | null): string => ROLE_LABEL[r as Role] ?? 'Team member'

/** How a show sells (api/app/services/sale_mode.py). */
export const SALE_MODE_LABEL: Record<SaleMode, string> = {
  winners_pick: 'Winners pick in chat',
  listing: 'One product per listing',
}
export const saleModeLabel = (m: string | undefined | null): string => SALE_MODE_LABEL[m as SaleMode] ?? 'Another way'
const SALE_MODE_HELP: Record<SaleMode, string> = {
  winners_pick: 'The winner types a shelf spot in chat, like A5, to say which product they want.',
  listing: 'Each listing is one product. The product comes from the listing, not from the chat.',
}
export const saleModeHelp = (m: string | undefined | null): string => SALE_MODE_HELP[m as SaleMode] ?? ''

export const SALE_STATUS_LABEL: Record<SaleStatus, string> = {
  detected: 'needs a decision',
  reserved: 'reserved',
  paid: 'paid',
  cancelled: 'cancelled',
  payment_failed: 'payment declined',
  fulfilled: 'shipped',
}
export const saleStatusLabel = (s: string): string => SALE_STATUS_LABEL[s as SaleStatus] ?? 'other'

/** Orders: `paid` means every item is paid, so the order is ready to pick. */
export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  open: 'Needs review',
  paid: 'Ready to pick',
  picking: 'Picking',
  packed: 'Packed',
  shipped: 'Shipped',
  cancelled: 'Cancelled',
}
/** The one verb that moves an order on from where it is. */
export const ORDER_NEXT_STEP: Partial<Record<OrderStatus, { label: string; to: OrderStatus }>> = {
  paid: { label: 'Start picking', to: 'picking' },
  picking: { label: 'Mark packed', to: 'packed' },
  packed: { label: 'Mark shipped', to: 'shipped' },
}
/** The one step back a person may take (the API allows no other). */
export const ORDER_STEP_BACK: Partial<Record<OrderStatus, { label: string; to: OrderStatus }>> = {
  picking: { label: 'Back to Ready to pick', to: 'paid' },
  packed: { label: 'Back to Picking', to: 'picking' },
}
export const orderStatusLabel = (s: string): string => ORDER_STATUS_LABEL[s as OrderStatus] ?? 'other'

/** "lot #520" for a sale whose title carries Whatnot's lot number, else the chat line it came from. */
export const lotText = (s: Pick<Sale, 'lot' | 'seq'>) => (s.lot != null ? `lot #${s.lot}` : s.seq ? `line ${s.seq}` : 'this lot')

/** Lot types read from the listing title (api `rules.AUCTION_TYPES` / `LOT_LABELS`). */
const LOT_LABEL: Record<string, string> = { red: 'Red', mega: 'Mega', ultra: 'Ultra', bundle: 'Bundle', quick_hit: 'Quick Hit' }
export const lotLabel = (t: string | null | undefined): string => (t ? (LOT_LABEL[t] ?? '') : '')

/** What the stock-sheet import will do with a row. */
const IMPORT_ACTION: Record<string, string> = { create: 'new', set: 'update', unchanged: 'unchanged', skip: 'skip' }
export const importActionLabel = (a: string): string => IMPORT_ACTION[a] ?? 'skip'

/** What matching the payment file will do with a row. */
const RECONCILE_ACTION: Record<string, string> = {
  mark_paid: 'mark paid',
  cancel: 'cancel',
  decline: 'payment declined',
  unchanged: 'unchanged',
  unmatched: 'no matching sale',
  skip: 'skip',
  none: 'unchanged',
  uncovered: 'not in the file',
}
export const reconcileActionLabel = (a: string): string => RECONCILE_ACTION[a] ?? 'unchanged'

/** "3 update · 1 new" from a plan's {code: count} summary, in words. */
export const summaryWords = (summary: Record<string, number>, label: (code: string) => string): string =>
  Object.entries(summary)
    .filter(([, n]) => n)
    .map(([k, n]) => `${n} ${label(k)}`)
    .join(' · ')

/** Where a review item came from. */
export const reviewSourceLabel = (s: string): string => (s === 'ai' ? 'AI suggestion' : 'from the chat')

/** Why the Whatnot connection is not working, in the shop's words. */
const WHATNOT_PROBLEM: Record<string, string> = {
  token_rejected: 'Whatnot no longer accepts the access token. The account owner needs to make a new one in Seller Hub.',
  no_permission: 'The access token is missing a permission. The account owner can add it to the token in Seller Hub.',
  unreachable: 'Whatnot could not be reached the last time we tried.',
}
export const whatnotProblemLabel = (p: string): string => WHATNOT_PROBLEM[p] ?? 'Something is wrong with the connection.'

/** What each kind of reading from Whatnot is called. */
const WHATNOT_SYNC: Record<string, string> = {
  shows: 'Shows',
  orders: 'Orders and payments',
  open_orders: 'Orders Whatnot had not settled',
  shipments: 'Shipments and tracking',
  catalog: 'Products and stock',
  analytics: 'Show results',
  ledger: 'Fees and payouts',
}
export const whatnotSyncLabel = (r: string): string => WHATNOT_SYNC[r] ?? 'Other'

/** A show's state on Whatnot. */
const WHATNOT_SHOW_STATUS: Record<string, string> = { scheduled: 'Scheduled', live: 'Live', stopped: 'Paused', ended: 'Ended', cancelled: 'Cancelled' }
export const whatnotShowStatusLabel = (s: string): string => WHATNOT_SHOW_STATUS[s] ?? 'Other'

/** What Whatnot says about an order's payment. */
const WHATNOT_PAYMENT: Record<string, string> = { paid: 'paid', pending: 'payment pending', void: 'cancelled', failed: 'payment failed' }
export const whatnotPaymentLabel = (s: string): string => WHATNOT_PAYMENT[s] ?? 'other'

/** What applying Whatnot's order does to the app's sale. */
const WHATNOT_ORDER_ACTION: Record<string, string> = {
  unchanged: 'nothing to change',
  mark_paid: 'mark the sale paid',
  cancel: 'cancel the sale',
  decline: 'mark the payment declined',
  record: 'record the sale',
  recorded: 'sale recorded',
  reopen: 'wait for its product again',
  unmatched: 'send to Review',
  skip: 'left alone',
  uncovered: 'with no order on Whatnot',
}
export const whatnotOrderActionLabel = (a: string): string => WHATNOT_ORDER_ACTION[a] ?? 'left alone'

/** The same actions once applied, in the past tense ("2 marked paid · 1 sale recorded"). */
const WHATNOT_APPLIED: Record<string, string> = {
  mark_paid: 'marked paid',
  cancel: 'cancelled',
  decline: 'payment declined',
  recorded: 'newly recorded',
  reopen: 'waiting for a product again',
  gave_way: 'cancelled, the auction went to another win',
  unmatched: 'sent to Review',
  uncovered: 'with no order on Whatnot, sent to Review',
}
export const whatnotAppliedLabel = (a: string): string => WHATNOT_APPLIED[a] ?? 'left alone'

/** Where a buyer's shipping stands for a show. One package and one label per buyer is the rule. */
const SHIPPING_STATE: Record<string, string> = {
  none: 'Not in a package on Whatnot yet',
  wait: 'Bundle still open: the show is live',
  shared: 'Bundle still filling: this buyer is in another show that is live now',
  reading: "Waiting for the show's final list from Whatnot",
  ready: 'Ready for its label',
  second: 'A later win needs its own label',
  split: 'Two open packages: combine them in Seller Hub first',
  hold: 'On hold',
  pickup: 'Pickup: no label needed',
  other: 'Check this package in Seller Hub',
  labelled: 'Label ready',
  shipped: 'With the carrier',
  failed: 'Delivery failed',
}
export const shippingStateLabel = (s: string): string => SHIPPING_STATE[s] ?? 'Other'

/** A package's own status on Whatnot. */
const PACKAGE_STATUS: Record<string, string> = {
  created: 'No label yet',
  label_generated: 'Label ready',
  dropped_off: 'Dropped off',
  in_transit: 'In transit',
  delivered: 'Delivered',
  failed: 'Delivery failed',
}
export const packageStatusLabel = (s: string): string => PACKAGE_STATUS[s] ?? (s.startsWith('cancelled') ? 'Cancelled' : 'Other')

/** Where a shipped order's parcel is: every parcel of the buyer in that show, in one phrase. */
const PARCEL_STATE: Record<string, string> = {
  waiting: 'Waiting for the carrier',
  late: 'Not with the carrier yet',
  moving: 'On its way',
  delivered: 'Delivered',
  failed: 'Delivery failed',
}
export const parcelStateLabel = (s: string): string => PARCEL_STATE[s] ?? 'Check it on Whatnot'

/** What Whatnot wants done before a package ships. */
const PACKAGE_HOLD: Record<string, string> = {
  order_pending_cancellation: 'Do not pack: a cancellation is pending',
  package_review_required: 'Whatnot wants this package reviewed',
  country_of_origin_required: 'Country of origin is needed',
  itn_required: 'An export number is needed',
}
export const packageHoldLabel = (h: string): string => PACKAGE_HOLD[h] ?? 'Whatnot needs something done first'

/** Why Whatnot did not make a label when asked. */
const LABEL_PROBLEM: Record<string, string> = {
  needs_verification: 'Whatnot needs the seller to finish verification before it makes labels',
  no_permission: 'The access token may not make labels yet. The account owner can add that permission in Seller Hub',
  not_now: 'Whatnot cannot make this label right now. Open the package in Seller Hub to see why',
  unknown: 'Whatnot did not confirm it took the request. Check the package in Seller Hub before asking again',
}
export const labelProblemLabel = (p: string): string => LABEL_PROBLEM[p] ?? LABEL_PROBLEM.unknown!

/** The kinds of line in Whatnot's financial ledger. */
const LEDGER_KIND: Record<string, string> = {
  ORDER_EARNINGS: 'Order earnings',
  SHIPPING_CHARGE: 'Shipping charged to the shop',
  CARRIER_ADJUSTMENT: 'Carrier adjustments',
  CARRIER_ADJUSTMENT_REFUND: 'Carrier adjustments refunded',
  ORDER_REFUND: 'Refunds',
  ORDER_REFUND_SHIPPING_COSTS: 'Shipping on refunded orders',
  APPEALED_ORDER_REFUND: 'Refunds won back on appeal',
  APPEALED_ORDER_REFUND_SHIPPING_COSTS: 'Shipping won back on appeal',
  TIP: 'Tips',
}
export const ledgerKindLabel = (k: string): string => LEDGER_KIND[k] ?? 'Other'

/** The fees Whatnot lists on an order's earnings. */
const LEDGER_FEE: Record<string, string> = {
  commission: 'Commission',
  commission_tax: 'Tax on commission',
  processing_fee: 'Payment processing',
  processing_fee_tax: 'Tax on processing',
  seller_shipping: 'Shipping paid by the shop',
  coupon: 'Coupons',
}
export const ledgerFeeLabel = (f: string): string => LEDGER_FEE[f] ?? 'Other'

/** Why a show's results from Whatnot are not there (yet). */
const RESULTS_STATUS: Record<string, string> = {
  PENDING: 'Whatnot is still working out this show’s results. They usually arrive within a few hours of the show.',
  NOT_ENDED: 'Whatnot works out a show’s results after it ends.',
  WILL_NOT_BE_CALCULATED: 'Whatnot does not work out results for this show (cancelled, private or a rehearsal).',
}
export const resultsStatusLabel = (s: string | null): string => (s ? (RESULTS_STATUS[s] ?? 'Whatnot has no results for this show yet.') : 'Whatnot’s results for this show have not been read yet.')

const BUYER_FLAG: Record<string, string> = { vip: 'VIP', watch: 'Watch list' }
export const buyerFlagLabel = (f: string | null | undefined): string => (f ? (BUYER_FLAG[f] ?? 'Flagged') : 'No flag')

/** How a listing came to be linked. */
export const listingLinkLabel = (l: string | null): string => (l === 'code' ? 'matched by its code' : l === 'person' ? 'linked by the team' : l === 'off' ? 'not one of ours' : 'not linked yet')

/** What the pick worker found for a win, in words (services/picks.py). */
const PICK_HELD: Record<string, string> = {
  off: 'Assigning picks by themselves is switched off, so confirm it here.',
  unconfirmed: 'Not assigned by itself: Whatnot has not confirmed this win as paid.',
  clock: "Not assigned by itself: the laptop's clock could not be checked for this show.",
  doubt: 'Not assigned by itself: this sale has an open question.',
  near: "Not assigned by itself: it was typed within half a minute before this winner's next win, so it may be for that one.",
  unclear: 'Not assigned by itself: this winner typed more spots than can be matched to their wins.',
}
export function pickWords(pick: { state: string; spot?: string; spots?: string[]; name?: string; held?: string[]; extra?: { spot: string; name: string }[]; allowed?: number }): string {
  switch (pick.state) {
    case 'product': {
      // A win that may hold several products ("two for one") lists every spot and product.
      const all = [{ spot: pick.spot ?? '', name: pick.name ?? '' }, ...(pick.extra ?? [])]
      const typed = `Typed ${all.map((p) => p.spot).join(' and ')} in chat: ${all.map((p) => p.name).join(' + ')}.`
      const held = (pick.held ?? []).map((h) => (h === 'short' ? `Not assigned by itself: this win may hold ${pick.allowed ?? 2} products and the winner picked ${all.length}.` : (PICK_HELD[h] ?? '')))
      return [typed, ...held].filter(Boolean).join(' ')
    }
    case 'unknown_code':
      return `Typed ${pick.spot}. No product is on ${pick.spot} for this show.`
    case 'several':
      return `Typed ${(pick.spots ?? []).join(' and ')}: more spots than this win takes, so none was chosen.`
    case 'unread':
      return "This show's picks could not be read: a laptop's clock was out. Read the chat."
    case 'no_layout':
      return 'This show has no shelf layout, so no pick could be read.'
    default:
      return 'Did not pick: no shelf spot from this winner before the show ended.'
  }
}

/** What an order's unnamed items are waiting for. In a winner's-pick show that is the winner:
 * "waiting for pick" until the show is over, "did not pick" once the worker has looked. */
export function unknownWords(unk: { saleMode?: string | null; review?: { pick?: string | null } | null }[]): string {
  const n = unk.length
  if (unk.every((s) => s.review?.pick === 'none')) return n === 1 ? 'did not pick' : `${n} did not pick`
  if (unk.every((s) => s.saleMode === 'winners_pick' && !s.review?.pick)) return n === 1 ? 'waiting for pick' : `${n} waiting for pick`
  return n === 1 ? 'item not recognised' : `${n} items not recognised`
}
