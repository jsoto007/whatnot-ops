import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { apiGet, type BuyerFlag, type BuyerFlags } from '../lib/api'
import { buyerFlagLabel } from '../lib/labels'

/**
 * Every flagged or noted buyer's names, fetched once and shared by every page that shows a
 * buyer. A page still works without it (a failed read is simply no flags).
 */
function useBuyerFlags(): BuyerFlags {
  const q = useQuery({
    queryKey: ['buyer-flags'],
    queryFn: () => apiGet<{ flags: BuyerFlags }>('/api/buyers/flags'),
    staleTime: 60_000,
    retry: false,
  })
  return q.data?.flags ?? {}
}

/** The flag itself, in words. */
export function FlagChip({ flag }: { flag: BuyerFlag | null }) {
  if (!flag) return null
  return (
    <span className={`buyer-flag ${flag}`} data-testid="buyer-flag">
      {buyerFlagLabel(flag)}
    </span>
  )
}

/**
 * The team's flag beside a buyer's name (and "Note" when someone wrote one), linking to
 * the buyer's page. Nothing for a buyer nobody has said anything about. Never printed:
 * a packing slip can end up in the parcel.
 */
export function BuyerFlagChip({ name }: { name: string | null | undefined }) {
  const flags = useBuyerFlags()
  const mine = name ? flags[name.toLowerCase()] : undefined
  if (!name || !mine || (!mine.flag && !mine.note)) return null
  return (
    <Link to={`/app/buyers/${encodeURIComponent(name)}`} className={`buyer-flag ${mine.flag ?? 'noted'}`} data-testid="buyer-flag" title="Open this buyer's page">
      {mine.flag ? buyerFlagLabel(mine.flag) : 'Note'}
      {mine.flag && mine.note ? ' · note' : ''}
    </Link>
  )
}
