// Whatnot's live player footer, copied from a real show (giftexpress, 2026-10-24) with images,
// links and inline styles stripped. Only the winner, item, price and timer are parameters.
// One change: Whatnot's shipping line is a <button> inside the item <button>. React builds
// that fine, but parsed HTML can't nest buttons and would push the price block out of the
// player, so here it is a <span> with the same testid.

const status = (inner: string, avatar = 'C', colour = 'bg-lime-500 text-neutral-1000') =>
  `<div class="flex gap-1 items-center text-white"><div class="w-(--size) flex items-center justify-center"><span class="flex flex-col relative aspect-square size-6"><span class="rounded-full overflow-hidden self-stretch items-center flex flex-row justify-center w-full grow h-6 aspect-square ${colour}"><span class="block size-full overflow-hidden whitespace-nowrap font-semibold text-center">${avatar}</span></span><div class="absolute flex items-center justify-center size-1.5 left-4.5 top-4.5"></div></span></div><strong class="text-current inline font-sans text-caption1 leading-caption1 font-bold" data-testid="show-winning-status">${inner}</strong></div>`

// The line by the winner's avatar on a won item: someone's last bid, NOT the price.
const lastBid = (bid: string) =>
  `<div class="flex gap-1 items-center text-white"><div class="w-(--size) flex items-center justify-center"><span class="flex flex-col relative aspect-square size-5"><img loading="lazy" width="20" height="20" decoding="async" class="rounded-full overflow-hidden self-stretch items-center flex flex-row justify-center w-full grow h-5 aspect-square"><div class="absolute flex items-center justify-center size-[5px] left-[15px] top-[15px]"></div></span></div><strong class="text-current block text-caption1 leading-caption1 font-regular text-pretty">bid ${bid}</strong></div>`

const item = (title: string, bids: number) =>
  `<section class="flex items-stretch gap-1 justify-between"><button class="min-w-0 flex-1" type="button"><section class="flex items-stretch gap-1 justify-normal min-w-0 flex-1"><div class="mr-3 shrink-0"><div class="overflow-hidden relative shrink-0 aspect-square rounded-8 border-1 border-white"><img data-testid="show-product-image" loading="lazy" width="76" height="76" decoding="async" data-nimg="1" class="size-19 object-cover"></div></div><section class="flex flex-col items-stretch gap-1 justify-normal min-w-0 flex-1"><strong class="text-neutral-50 font-sans text-body1 leading-body1 line-clamp-(--clamp) font-bold text-pretty" data-testid="show-product-title">${title}</strong><strong class="text-neutral-50 font-sans text-body2 leading-body2 line-clamp-(--clamp) font-bold text-pretty">All Sales = FINAL / 100% Authentic / Please leave a 5 Star ⭐️/ Listen for item specifics / Happy bidding!!</strong><section class="flex items-stretch gap-1 justify-normal"><strong class="text-neutral-50 block text-body2 leading-body2 font-regular text-pretty tabular-nums relative after:content-['•'] after:ml-1 last:after:content-['']">${bids} Bids</strong></section><section class="flex items-center gap-2 justify-normal h-5 w-full overflow-hidden"><span class="contents" data-pinned-item-ldp-nav-skip=""><span class="h-4" data-testid="show-shipping-info"><strong class="text-neutral-transparent-white-70 block text-body2 leading-body2 font-regular text-pretty whitespace-nowrap">Shipping is $7.75 + Taxes</strong></span></span><div class="shrink-0"><section class="flex items-stretch gap-2 justify-normal"></section></div></section></section></section></button>`

const priceAnd = (price: string, second: string) =>
  `<section class="flex flex-col items-stretch gap-1 justify-normal ml-3"><strong class="text-right text-balance text-neutral-50 block font-sans text-body1 leading-body1 font-semibold text-pretty tabular-nums">${price}</strong>${second}</section></section>`

const sold = `<strong class="text-right text-balance text-red-500 block font-sans text-body1 leading-body1 font-semibold text-pretty">Sold</strong>`
const timer = (t: string) =>
  `<section class="flex items-center gap-1 justify-end"><div class="size-3"><svg font-size="12" height="32" preserveAspectRatio="xMidYMid meet" viewBox="0 0 512 512" width="32" xmlns="http://www.w3.org/2000/svg"></svg></div><div class="min-w-10"><strong class="text-right text-balance text-yellow-500 block font-sans text-body1 leading-body1 font-semibold text-pretty tabular-nums" data-testid="show-timer">${t}</strong></div></section>`

const wrap = (footer: string) =>
  `<section class="relative w-full h-full min-h-0 max-h-dvh lg:max-h-screen-minus-header lg:rounded" data-testid="live-player"><section class="LivePlayer_livePlayer__WDoms"><footer class="LivePlayer_livePlayerFooter__UDpaa">${footer}</footer></section></section>`

export const DEFAULT_TITLE = '$1 PERFUMES/ COLOGNES/ NICHE WITH  SAM #760'

/** An item between auctions: no status line, price and countdown. */
export const idle = (title = DEFAULT_TITLE, price = '$1', t = '00:20') => wrap(item(title, 0) + priceAnd(price, timer(t)))

/** Bidding: "<name> is Winning!", current price and countdown. */
export const bidding = (name: string, price: string, title = DEFAULT_TITLE, t = '00:09') =>
  wrap(
    `<div class="flex gap-1 items-center z-header">${status(`${name} is <strong class="text-yellow-500 inline font-sans text-caption1 leading-caption1 font-bold">Winning!</strong>`, name[0]!.toUpperCase(), 'bg-purple-500 text-neutral-0')}</div>` +
      item(title, 12) +
      priceAnd(price, timer(t)),
  )

/** Ended: "<name> won!", the last-bid line, final price and "Sold". */
export const won = (name: string, price: string, title = DEFAULT_TITLE, bid = '$183.00') =>
  wrap(
    `<div class="flex gap-1 items-center z-header">${status(`${name} <strong class="text-yellow-500 inline font-sans text-caption1 leading-caption1 font-bold">won!</strong>`, name[0]!.toUpperCase())}${lastBid(bid)}</div>` +
      item(title, 32) +
      priceAnd(price, sold),
  )
