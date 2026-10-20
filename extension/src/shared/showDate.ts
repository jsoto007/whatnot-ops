/** Show date in Chicago time; before 5 AM counts as the night before (same rule as v1.2). */
export function showDate(iso?: string | null, zone = 'America/Chicago'): string {
  const d = new Date(iso || Date.now())
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hour12: false,
    })
      .formatToParts(d)
      .map((p) => [p.type, p.value]),
  ) as Record<string, string>
  let date = new Date(Date.UTC(+parts.year!, +parts.month! - 1, +parts.day!))
  if (+parts.hour! % 24 < 5) date = new Date(date.getTime() - 86_400_000)
  return date.toISOString().slice(0, 10)
}
