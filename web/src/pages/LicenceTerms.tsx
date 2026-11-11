import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Markdown } from '../components/Markdown'
import { LICENCE_FILE_URL, LICENCE_TITLE } from '../lib/labels'

/**
 * The licence as a page people can read (/license): the sign-up box, the owner's banner and
 * the user agreement all open it. Its words are LICENSE.md itself, the file the build serves
 * beside the app, so the page can never say something the file does not.
 */
export function LicenceTerms() {
  const [text, setText] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    window.scrollTo(0, 0)
    fetch(LICENCE_FILE_URL)
      .then((res) => {
        // A path with no file behind it answers with the app's own page (api/app/spa.py):
        // only the file itself will do.
        if (!res.ok || (res.headers.get('content-type') ?? '').startsWith('text/html')) throw new Error('not the licence file')
        return res.text()
      })
      .then((body) => {
        if (live) setText(body)
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [])

  return (
    <article className="card legal legal-doc" aria-label={LICENCE_TITLE} aria-busy={text === null && !failed}>
      <Link to="/" className="legal-back">
        Back to Sales Ops
      </Link>
      {text !== null ? (
        <>
          <Markdown source={text} headingShift={1} />
          <p className="muted small no-print">
            These are the terms word for word. You can also <a href={LICENCE_FILE_URL}>open them as a plain text file</a> to save a copy.
          </p>
        </>
      ) : failed ? (
        <>
          <h2>{LICENCE_TITLE}</h2>
          <p role="alert">
            The terms could not be shown on this page. <a href={LICENCE_FILE_URL}>Open them as a plain text file</a> instead.
          </p>
        </>
      ) : (
        <p className="muted">Loading the terms…</p>
      )}
    </article>
  )
}
