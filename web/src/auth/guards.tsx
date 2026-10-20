import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from './useAuth'

export function RequireAuth() {
  const { me, loading } = useAuth()
  const loc = useLocation()
  if (loading) return <p className="muted">Loading…</p>
  if (!me) return <Navigate to="/login" replace state={{ from: loc.pathname }} />
  return <Outlet />
}

export function RequirePermission({ permission }: { permission: string }) {
  const { can } = useAuth()
  if (!can(permission)) return <p className="card">You do not have access to this section.</p>
  return <Outlet />
}

export function RedirectIfAuthed() {
  const { me, loading } = useAuth()
  if (loading) return <p className="muted">Loading…</p>
  if (me) return <Navigate to="/app" replace />
  return <Outlet />
}
