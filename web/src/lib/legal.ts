/** What the footer and the two legal pages (user agreement, privacy) share. */

/** Whatnot's Legal Center: its Terms of Service and the policies they pull in. */
export const WHATNOT_POLICIES_URL = 'https://www.whatnot.com/terms'

/** Shown at the top of both legal pages. Change it whenever their wording changes. */
export const LEGAL_UPDATED = 'November 11, 2026'

/** The contact address baked in at build time (VITE_SUPPORT_EMAIL). Unset or malformed: no
 *  Contact link, and the pages send questions to the shop's owner instead. */
export function supportEmail(): string | null {
  const value = String(import.meta.env.VITE_SUPPORT_EMAIL ?? '').trim()
  return /^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(value) ? value : null
}
