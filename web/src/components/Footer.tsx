import { Link } from 'react-router'
import { WHATNOT_POLICIES_URL, supportEmail } from '../lib/legal'

/** The small print under every page, signed in or not: the user agreement, privacy,
 *  Whatnot's own rules, and that Sales Ops changes when those rules do. Never printed. */
export function Footer() {
  const email = supportEmail()
  return (
    <footer className="site-footer">
      <nav aria-label="Legal">
        <Link to="/terms">User agreement</Link>
        <Link to="/privacy">Privacy</Link>
        <a href={WHATNOT_POLICIES_URL} target="_blank" rel="noopener noreferrer">
          Whatnot's terms and policies<span className="sr-only"> (opens in a new tab)</span>
        </a>
        {email && <a href={`mailto:${email}`}>Contact</a>}
      </nav>
      <p>
        Sales Ops is built to follow Whatnot's policies. What it does, and how you may use it, can change to stay in line with them. By using Sales Ops
        you accept the user agreement.
      </p>
      <p>© {new Date().getFullYear()} Sales Ops. An independent tool, not affiliated with, sponsored by or endorsed by Whatnot.</p>
    </footer>
  )
}
