import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router'
import { LEGAL_UPDATED, WHATNOT_POLICIES_URL, supportEmail } from '../lib/legal'

/** The pages the footer links to. Open to everyone, signed in or not. Keep every statement
 *  here true to what the app does: Privacy lists what is really stored and who really gets it. */

function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  // Reached from the foot of a long page: start at the top, not where that page was scrolled to.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [])
  return (
    <article className="card legal">
      <Link to="/" className="legal-back">
        Back to Sales Ops
      </Link>
      <h2>{title}</h2>
      <p className="muted small">Last updated {LEGAL_UPDATED}</p>
      {children}
    </article>
  )
}

function WhatnotPolicies({ children }: { children: ReactNode }) {
  return (
    <a href={WHATNOT_POLICIES_URL} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  )
}

function Questions() {
  const email = supportEmail()
  return (
    <>
      <h3>Questions</h3>
      <p>
        Ask the owner of your shop's account
        {email && (
          <>
            , or write to <a href={`mailto:${email}`}>{email}</a>
          </>
        )}
        .
      </p>
    </>
  )
}

export function Terms() {
  return (
    <LegalPage title="User agreement">
      <p>
        Sales Ops is a private tool that a Whatnot seller's team uses to run its live shows: sales, stock, orders and shipping. This agreement
        covers how you may use it. By signing in or using Sales Ops you accept it.
      </p>

      <h3>Who may use it</h3>
      <p>
        You may use Sales Ops only with an account the shop's owner created for you or invited you to, and only for the shop's work. Each person
        has their own account. Do not share yours.
      </p>

      <h3>Whatnot's policies come first</h3>
      <p>
        Sales Ops is an independent tool. It is not made by, affiliated with, sponsored by or endorsed by Whatnot. It is built to follow{' '}
        <WhatnotPolicies>Whatnot's terms and policies</WhatnotPolicies>, and it does not replace them: everything you do on Whatnot, with or
        without Sales Ops, is still covered by Whatnot's terms, its community guidelines and its rules for sellers.
      </p>
      <p>
        Because of that, Sales Ops can change. When Whatnot changes its policies, its site or the access it gives sellers, a feature here may work
        differently, be limited, be paused or be removed, sometimes without notice. If anything in Sales Ops seems to conflict with a Whatnot
        policy, follow Whatnot's policy and tell the shop's owner.
      </p>

      <h3>Your account</h3>
      <ul>
        <li>Keep your password to yourself, and tell the shop's owner at once if you think someone else has used your account.</li>
        <li>The shop's owner decides what each person can see and do, and can turn an account off at any time.</li>
        <li>Changes you make are recorded with your name, so the team can see who did what.</li>
      </ul>

      <h3>Fair use</h3>
      <p>Do not:</p>
      <ul>
        <li>use Sales Ops, or anything you see in it, for anything but the shop's work;</li>
        <li>share or use buyer details (usernames, what someone bought, the team's notes) outside the shop, or in a way Whatnot does not allow;</li>
        <li>try to reach data or accounts that are not yours, get around limits, or interfere with how Sales Ops runs;</li>
        <li>copy, resell or rebuild Sales Ops.</li>
      </ul>

      <h3>What the numbers mean</h3>
      <p>
        Whatnot is the record of what was sold, what was paid and where a parcel is. Sales Ops reads that record and adds the shop's own work on
        top: which product, the gifts, picking and packing. Captured chat, matched sales and suggestions, including those made by AI, can be
        wrong, so a person should check before acting on them. When it matters, check Whatnot.
      </p>

      <h3>No guarantees</h3>
      <p>
        Sales Ops is provided as it is. It can be unavailable, slow or wrong, and features can change or be removed. To the extent the law
        allows, the people who make and run Sales Ops are not responsible for losses that come from relying on it, such as a missed sale, a wrong
        shipment or a wrong count.
      </p>

      <h3>The shop's own agreement</h3>
      <p>
        This page is for each person who signs in. The shop may also have a license or other written agreement with the maker of Sales Ops,
        covering things such as who owns the shop's data and who owns the software. Where that agreement and this page say different things,
        the shop's agreement applies.
      </p>

      <h3>Changes to this agreement</h3>
      <p>
        This agreement can be updated, for example when Whatnot's policies change. The date at the top shows the latest version. Using Sales Ops
        after a change means you accept it.
      </p>

      <Questions />
    </LegalPage>
  )
}

export function Privacy() {
  return (
    <LegalPage title="Privacy">
      <p>
        This page says what Sales Ops keeps, why, and who can see it. It covers the people on a shop's team who sign in, and the Whatnot buyers
        whose purchases the shop handles here.
      </p>

      <h3>What Sales Ops keeps</h3>
      <ul>
        <li>
          <b>Your account.</b> Your name, email address, role and when you last signed in. Your password is stored only in a scrambled form that
          cannot be read back.
        </li>
        <li>
          <b>What the team does.</b> A record of changes and sign-ins, with who made each one and when. Each sign-in, and each failed attempt
          to sign in to an account, is recorded with the network address it came from. So is the shop's acceptance of its license for Sales
          Ops: which version, who accepted it and when.
        </li>
        <li>
          <b>The shop's records.</b> Shows, products, stock, sales, orders, shipments and notes.
        </li>
        <li>
          <b>Live show chat.</b> The public chat of the shop's shows, with the Whatnot usernames shown in it, as captured on the show laptop.
        </li>
        <li>
          <b>Buyers.</b> Their Whatnot user number and usernames, what they won in the shop's shows, and any note or flag the team adds. No
          street address, email address or phone number is kept. Shipping labels are fetched from Whatnot when someone prints them and are not
          saved.
        </li>
        <li>
          <b>Technical details.</b> The network address you connect from is counted for a short time to block abuse, is kept with the record
          of sign-ins described above, and may appear in server logs.
        </li>
      </ul>

      <h3>How it is used</h3>
      <p>
        Only to run the shop: matching sales to products, picking, packing and shipping orders, producing the shop's reports and keeping accounts
        secure. It is not sold, not used for advertising and not used to follow anyone around the web.
      </p>

      <h3>Who else handles it</h3>
      <ul>
        <li>The shop's team, each person according to their role.</li>
        <li>Whatnot. Sales Ops reads the shop's own Whatnot data and sends Whatnot the shop's requests, such as asking for a shipping label.</li>
        <li>The developer who builds and maintains Sales Ops, to keep it running and fix problems.</li>
        <li>
          The companies that run it for the shop: hosting, the network service in front of the hosting that carries every request to it and
          guards it against attacks, and email delivery for invitations and password resets.
        </li>
        <li>An AI service, when AI suggestions are turned on. Show chat and sales are sent to it so it can suggest matches.</li>
        <li>Google Fonts. The page loads its typeface from Google, so your browser contacts Google when a page opens.</li>
      </ul>

      <h3>Cookies and browser storage</h3>
      <p>
        Sales Ops itself sets one cookie. It keeps you signed in for up to 14 days and the site needs it to work. There are no advertising or
        analytics cookies. Your browser also remembers a few display choices, such as whether the menu is open.
      </p>

      <h3>How long it is kept</h3>
      <p>
        The shop's records, the captured chat and the record of changes and sign-ins, with the network addresses in it, are kept for as long as
        the shop uses Sales Ops. The counts used to block abuse are deleted after about a day. Expired sign-in links and laptop pairing codes are
        deleted after about a month.
      </p>

      <h3>Your choices</h3>
      <p>
        To see or correct your account details, or to have your account closed, ask the owner of your shop's account. If you bought from a shop
        on Whatnot and want to know what it holds about you here, message the shop on Whatnot.
      </p>

      <h3>Changes to this page</h3>
      <p>This page can be updated as Sales Ops or Whatnot's policies change. The date at the top shows the latest version.</p>

      <Questions />
    </LegalPage>
  )
}
