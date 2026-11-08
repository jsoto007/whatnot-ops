/**
 * The chat as a CSV file that Excel opens straight away: Time, Username, Message, Flagged.
 *
 * Chat is attacker-controlled. A cell starting with = + - @ (or a tab or carriage return)
 * runs as a formula when Excel opens the file, so such a cell gets a leading apostrophe
 * (OWASP's advice; the apostrophe shows in the cell, which is the price of not running the
 * formula). Every field is quoted with inner quotes doubled, lines end CRLF, and the file
 * starts with a byte-order mark so Excel reads it as UTF-8 (emoji, accents).
 */
export interface ChatCsvLine {
  /** Local date and time, "2026-11-07 20:01:05" (see csvTime). */
  time: string
  username: string
  message: string
  flagged?: boolean
}

export const CHAT_CSV_HEADER = ['Time', 'Username', 'Message', 'Flagged'] as const
const FORMULA_START = /^[=+\-@\t\r]/

export function csvCell(value: string): string {
  const safe = FORMULA_START.test(value) ? "'" + value : value
  return '"' + safe.replace(/"/g, '""') + '"'
}

export function chatCsv(lines: readonly ChatCsvLine[]): string {
  const rows: readonly (readonly string[])[] = [CHAT_CSV_HEADER, ...lines.map((l) => [l.time, l.username, l.message, l.flagged ? 'yes' : ''])]
  return '\uFEFF' + rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

/** "2026-11-07 20:01:05" in this computer's zone: Excel reads it as a date and time. */
export function csvTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** CSV text as rows of fields (RFC 4180: quoted fields, doubled quotes, line breaks inside quotes). */
export function parseCsv(text: string): string[][] {
  const src = text.startsWith('\uFEFF') ? text.slice(1) : text
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < src.length; i++) {
    const c = src[i]!
    if (quoted) {
      if (c !== '"') field += c
      else if (src[i + 1] === '"') {
        field += '"'
        i++
      } else quoted = false
    } else if (c === '"') quoted = true
    else if (c === ',') {
      row.push(field)
      field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++
      row.push(field)
      field = ''
      rows.push(row)
      row = []
    } else field += c
  }
  if (field || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

/** A chat CSV written by chatCsv (header Time, Username, Message, optionally Flagged); null when `text` is not one. */
export function parseChatCsv(text: string): ChatCsvLine[] | null {
  const rows = parseCsv(text)
  const head = (rows[0] ?? []).map((h) => h.trim().toLowerCase())
  const [time, user, msg, flag] = ['time', 'username', 'message', 'flagged'].map((h) => head.indexOf(h)) as [number, number, number, number]
  if (time < 0 || user < 0 || msg < 0) return null
  // The apostrophe csvCell put in front of a formula-looking cell is not part of the chat.
  const plain = (v: string) => (v.length > 1 && v[0] === "'" && FORMULA_START.test(v.slice(1)) ? v.slice(1) : v)
  return rows
    .slice(1)
    .filter((r) => r.some((v) => v !== ''))
    .map((r) => ({ time: r[time] ?? '', username: plain(r[user] ?? ''), message: plain(r[msg] ?? ''), flagged: flag >= 0 && (r[flag] ?? '') !== '' }))
}
