import { Link } from 'react-router'
import type { LiveGap } from '../lib/api'
import { liveGapWords } from '../lib/labels'

/**
 * Whatnot says a show is live but no laptop is recording it (or it began late, or its chat
 * stopped). Said to every role: chat wins, gifts and "which product" picks are lost meanwhile.
 * `link`: on the dashboard, each line opens the show; on the show's own page there is no need.
 */
export function LiveGaps({ gaps, link = false }: { gaps: LiveGap[]; link?: boolean }) {
  if (!gaps.length) return null
  return (
    <div className="card small warn-card" role="status" data-testid="live-gaps">
      <b>{gaps.length === 1 ? 'A live show needs its laptop.' : 'Live shows need their laptops.'}</b>
      {gaps.map((g, i) => (
        <div key={`${g.code}-${i}`} data-testid={`live-gap-${g.code}`}>
          {g.title ? <b>{g.title}: </b> : null}
          {liveGapWords(g.code, g.minutes)}
          {link && g.showId !== null && (
            <>
              {' '}
              <Link to={`/app/shows/${g.showId}`}>Open the show</Link>
            </>
          )}
        </div>
      ))}
    </div>
  )
}
