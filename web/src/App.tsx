import { BrowserRouter, Link, Navigate, Outlet, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/useAuth'
import { RedirectIfAuthed, RequireAuth, RequirePermission } from './auth/guards'
import { AcceptInvite } from './pages/AcceptInvite'
import { Login } from './pages/Login'
import { Forgot, Reset } from './pages/Password'
import { Signup } from './pages/Signup'
import { Dashboard } from './pages/app/Dashboard'
import { Inventory } from './pages/app/Inventory'
import { AppLayout } from './pages/app/Layout'
import { OrderDetail, Orders, PickList } from './pages/app/Orders'
import { OrderSlips } from './pages/app/OrderSlips'
import { Products } from './pages/app/Products'
import { Reports } from './pages/app/Reports'
import { Settings } from './pages/app/Settings'
import { Review } from './pages/app/Review'
import { ShowDetail } from './pages/app/ShowDetail'
import { Shows } from './pages/app/Shows'
import { Team } from './pages/app/Team'

function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="app-header">
        <h1>
          <Link to="/" className="brand-link">
            <img src="/logo.png" alt="" className="brand-logo" />
            Sales Ops
          </Link>
        </h1>
      </header>
      <main className="app-main">{children}</main>
    </>
  )
}

function Home() {
  const { me, loading } = useAuth()
  if (loading) return <p className="muted">Loading…</p>
  return <Navigate to={me ? '/app' : '/login'} replace />
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route
            element={
              <PublicShell>
                <RedirectIfAuthed />
              </PublicShell>
            }
          >
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/forgot" element={<Forgot />} />
          </Route>
          <Route
            element={
              <PublicShell>
                <RouteOutlet />
              </PublicShell>
            }
          >
            <Route path="/invite/:token" element={<AcceptInvite />} />
            <Route path="/reset/:token" element={<Reset />} />
          </Route>
          <Route path="/app" element={<RequireAuth />}>
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="shows" element={<Shows />} />
              <Route path="shows/:id" element={<ShowDetail />} />
              <Route path="orders" element={<Orders />} />
              <Route path="orders/pick-list/:showId" element={<PickList />} />
              <Route path="orders/slips/:showId" element={<OrderSlips />} />
              <Route path="orders/:id" element={<OrderDetail />} />
              <Route path="inventory" element={<Inventory />} />
              <Route path="products" element={<Products />} />
              <Route element={<RequirePermission permission="review.act" />}>
                <Route path="review" element={<Review />} />
                <Route path="review/:showId" element={<Review />} />
              </Route>
              <Route element={<RequirePermission permission="reports.view" />}>
                <Route path="reports" element={<Reports />} />
              </Route>
              <Route element={<RequirePermission permission="team.view" />}>
                <Route path="team" element={<Team />} />
              </Route>
              <Route element={<RequirePermission permission="devices.manage" />}>
                <Route path="settings" element={<Settings />} />
              </Route>
            </Route>
          </Route>
          <Route path="*" element={<PublicShell><p>Not found.</p></PublicShell>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

function RouteOutlet() {
  return <Outlet />
}
