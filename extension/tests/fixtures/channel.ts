// Live pages around the seller's (channel's) name, with made-up names. NOT copied from
// whatnot.com: nobody has checked where the real page shows the seller's name. These are the
// shapes capture/channel.ts guesses (CHANNEL_SOURCES), plus the things it must never read:
// a winner in the player and people in the chat.

/** The player, with the seller's name where the reader's table looks for it. */
export const sellerName = (seller: string) => `<span data-testid="show-host-username">${seller}</span>`

/** The seller's name as a link to their profile, in a host box the table guesses. */
export const sellerLink = (seller: string, text = seller) => `<div data-testid="show-host"><a href="/user/${seller}">${text}</a></div>`

/** A win in the player footer, the winner linked to their profile (real links were stripped from player.ts). */
export const winnerFooter = (winner: string) =>
  `<footer><div><a href="/user/${winner}"><strong data-testid="show-winning-status">${winner} <strong>won!</strong></strong></a></div></footer>`

/** The chat: people's names, some linked to their profiles. */
export const chat = (...people: string[]) =>
  `<div class="chat" id="chat">${people.map((p) => `<div class="msg"><a href="/user/${p}" class="msg-user">${p}</a><span class="msg-text">hi from ${p}</span></div>`).join('')}</div>`

export const page = (player: string, chatHtml = chat('fern_buyer', 'oak_viewer')) =>
  `<main><section data-testid="live-player">${player}</section>${chatHtml}</main>`
