import { NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../../auth/useAuth'

const SECTIONS: { to: string; label: string; permission: string; end?: boolean }[] = [
  { to: '/app', label: 'Dashboard', permission: 'shows.view', end: true },
  { to: '/app/shows', label: 'Shows', permission: 'shows.view' },
  { to: '/app/orders', label: 'Orders', permission: 'orders.view' },
  { to: '/app/inventory', label: 'Inventory', permission: 'inventory.view' },
  { to: '/app/products', label: 'Products', permission: 'inventory.view' },
  { to: '/app/review', label: 'Review', permission: 'review.act' },
  { to: '/app/reports', label: 'Reports', permission: 'reports.view' },
  { to: '/app/team', label: 'Team', permission: 'team.view' },
  { to: '/app/settings', label: 'Settings', permission: 'devices.manage' },
]

export function AppLayout() {
  const { me, can, logout } = useAuth()
  const nav = useNavigate()
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">{me?.organization.name}</div>
        <nav aria-label="Sections">
          {SECTIONS.filter((s) => can(s.permission)).map((s) => (
            <NavLink key={s.to} to={s.to} end={s.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              {s.label}
            </NavLink>
          ))}
        </nav>
        <div className="me">
          <div>
            <b>{me?.user.name}</b>
            <div className="muted small">{me?.user.role}</div>
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
