import { NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../../auth/useAuth'

const GROUPS: { title: string; items: { to: string; label: string; permission: string; ico: string; end?: boolean }[] }[] = [
  {
    title: 'Operate',
    items: [
      { to: '/app', label: 'Dashboard', permission: 'shows.view', ico: '◫', end: true },
      { to: '/app/shows', label: 'Shows', permission: 'shows.view', ico: '▶' },
      { to: '/app/orders', label: 'Orders', permission: 'orders.view', ico: '⊟' },
      { to: '/app/review', label: 'Review', permission: 'review.act', ico: '⚑' },
    ],
  },
  {
    title: 'Catalog',
    items: [
      { to: '/app/inventory', label: 'Inventory', permission: 'inventory.view', ico: '▤' },
      { to: '/app/products', label: 'Products', permission: 'inventory.view', ico: '◈' },
    ],
  },
  {
    title: 'Admin',
    items: [
      { to: '/app/reports', label: 'Reports', permission: 'reports.view', ico: '▥' },
      { to: '/app/team', label: 'Team', permission: 'team.view', ico: '☺' },
      { to: '/app/settings', label: 'Settings', permission: 'devices.manage', ico: '⚙' },
    ],
  },
]

export function AppLayout() {
  const { me, can, logout } = useAuth()
  const nav = useNavigate()
  const initials = (me?.organization.name ?? 'W').slice(0, 1).toUpperCase()
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" aria-hidden>
            {initials}
          </span>
          <span>{me?.organization.name}</span>
        </div>
        <nav aria-label="Sections">
          {GROUPS.map((g) => {
            const items = g.items.filter((i) => can(i.permission))
            if (!items.length) return null
            return (
              <div key={g.title} style={{ display: 'contents' }}>
                <div className="nav-group">{g.title}</div>
                {items.map((s) => (
                  <NavLink key={s.to} to={s.to} end={s.end} className={({ isActive }) => (isActive ? 'active' : '')}>
                    <span className="ico" aria-hidden>
                      {s.ico}
                    </span>
                    {s.label}
                  </NavLink>
                ))}
              </div>
            )
          })}
        </nav>
        <div className="me">
          <div>
            <b>{me?.user.name}</b>
            <div className="muted small" style={{ color: '#8fa3c2' }}>
              {me?.user.role}
            </div>
          </div>
          <button
            className="btn small"
            onClick={async () => {
              await logout()
              nav('/login')
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
