import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { Footer } from '../../components/Footer'
import { Icon, type IconName } from '../../components/Icon'
import { apiGet, type NavCounts } from '../../lib/api'
import { roleLabel } from '../../lib/labels'
import { UpdateBanner } from './settings/UpdateExtension'
import { WhatnotBanner } from './settings/WhatnotConnection'

const GROUPS: { title: string; items: { to: string; label: string; permission: string; icon: IconName; end?: boolean }[] }[] = [
  {
    title: 'Operate',
    items: [
      { to: '/app', label: 'Dashboard', permission: 'shows.view', icon: 'dashboard', end: true },
      { to: '/app/shows', label: 'Shows', permission: 'shows.view', icon: 'shows' },
      { to: '/app/orders', label: 'Orders', permission: 'orders.view', icon: 'orders' },
      { to: '/app/buyers', label: 'Buyers', permission: 'buyers.view', icon: 'buyers' },
      { to: '/app/review', label: 'Review', permission: 'review.act', icon: 'review' },
    ],
  },
  {
    title: 'Catalog',
    items: [
      { to: '/app/inventory', label: 'Inventory', permission: 'inventory.view', icon: 'inventory' },
      { to: '/app/products', label: 'Products', permission: 'inventory.view', icon: 'products' },
    ],
  },
  {
    title: 'Admin',
    items: [
      { to: '/app/reports', label: 'Reports', permission: 'reports.view', icon: 'reports' },
      { to: '/app/team', label: 'Team', permission: 'team.view', icon: 'team' },
      { to: '/app/settings', label: 'Settings', permission: 'devices.manage', icon: 'settings' },
    ],
  },
]

const STORAGE_KEY = 'wn.sidebar'

/** Icons only until someone opens it; their choice is remembered. */
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'expanded'
  } catch {
    return true
  }
}

export function AppLayout() {
  const { me, can, logout } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [open, setOpen] = useState(false)
  const countsQ = useQuery({
    queryKey: ['nav-counts'],
    queryFn: () => apiGet<NavCounts>('/api/dashboard/counts'),
    enabled: can('shows.view'),
    refetchInterval: 30_000,
  })
  const counts = countsQ.data
  const refetchCounts = countsQ.refetch
  const mayCount = can('shows.view')
  // Badges follow the user around: refresh them on every navigation, not only on the 30 s poll.
  useEffect(() => {
    if (mayCount) void refetchCounts()
  }, [loc.pathname, mayCount, refetchCounts])
  const badge = (to: string): { text: string; tone: 'live' | 'count' } | null => {
    if (!counts) return null
    if (to === '/app/shows' && counts.live) return { text: 'Live', tone: 'live' }
    if (to === '/app/orders' && counts.toFulfil) return { text: String(counts.toFulfil), tone: 'count' }
    if (to === '/app/review' && counts.openReviews) return { text: String(counts.openReviews), tone: 'count' }
    return null
  }

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(STORAGE_KEY, c ? 'expanded' : 'collapsed')
      } catch {
        /* private mode */
      }
      return !c
    })
  }, [])

  // The drawer closes on navigation and on Escape; the page does not scroll behind it.
  useEffect(() => setOpen(false), [loc.pathname])
  useEffect(() => {
    document.body.classList.toggle('drawer-open', open)
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const userInitials = (me?.user.name ?? '?')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <div className={'app' + (collapsed ? ' collapsed' : '')}>
      <header className="topbar">
        <button className="icon-btn" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)} data-testid="open-menu">
          <Icon name="menu" size={22} />
        </button>
        <span className="mark" aria-hidden>
          <img src="/logo.png" alt="" />
        </span>
        <span className="topbar-title">{me?.organization.name}</span>
        <span className="avatar small" title={me?.user.name}>
          {userInitials}
        </span>
      </header>
      {open && <div className="scrim" onClick={() => setOpen(false)} data-testid="scrim" />}
      <aside className={'sidebar' + (open ? ' open' : '')} aria-label="Sidebar">
        <div className="brand">
          <span className="mark" aria-hidden>
            <img src="/logo.png" alt="" />
          </span>
          <span className="label brand-name">
            <b>{me?.organization.name}</b>
            <span>Sales Ops</span>
          </span>
          <button className="icon-btn drawer-close" aria-label="Close menu" onClick={() => setOpen(false)}>
            <Icon name="close" size={20} />
          </button>
        </div>
        <nav aria-label="Sections">
          {GROUPS.map((g) => {
            const items = g.items.filter((i) => can(i.permission))
            if (!items.length) return null
            return (
              <div key={g.title} className="nav-section">
                <div className="nav-group">{g.title}</div>
                {items.map((s) => {
                  const b = badge(s.to)
                  return (
                    <NavLink key={s.to} to={s.to} end={s.end} title={collapsed ? s.label : undefined} className={({ isActive }) => (isActive ? 'active' : '')}>
                      <span className="nav-ico">
                        <Icon name={s.icon} />
                        {b && <span className={'nav-dot ' + b.tone} aria-hidden />}
                      </span>
                      <span className="label">{s.label}</span>
                      {b && <span className={'nav-badge ' + b.tone}>{b.text}</span>}
                    </NavLink>
                  )
                })}
              </div>
            )
          })}
        </nav>
        <div className="me">
          <span className="avatar" title={me?.user.name}>
            {userInitials}
          </span>
          <div className="label">
            <b>{me?.user.name}</b>
            <div className="role">{roleLabel(me?.user.role)}</div>
          </div>
          <button
            className="icon-btn"
            title="Sign out"
            aria-label="Sign out"
            onClick={async () => {
              await logout()
              nav('/login')
            }}
          >
            <Icon name="logout" />
          </button>
        </div>
        <button className="collapse-btn" onClick={toggleCollapsed} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} data-testid="collapse-sidebar">
          <Icon name="chevron" size={16} />
          <span className="label">Collapse</span>
        </button>
      </aside>
      <div className="main-col">
        <main className="content">
          <WhatnotBanner show={!!counts?.whatnotProblem} />
          <UpdateBanner enabled={can('shows.view')} />
          <Outlet />
        </main>
        <Footer />
      </div>
    </div>
  )
}
