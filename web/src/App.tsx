import { lazy, Suspense, type ComponentType } from 'react'
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/useAuth'
import { RedirectIfAuthed, RequireAuth, RequirePermission } from './auth/guards'
import { Footer } from './components/Footer'
import { Login } from './pages/Login'
import { Signup } from './pages/Signup'
import { Dashboard } from './pages/app/Dashboard'
import { AppLayout } from './pages/app/Layout'
import { Shows } from './pages/app/Shows'

/** A page loaded on first visit, with a small fallback while its code arrives (keeps the sidebar and header in place). */
function lazyPage<K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) {
  const Page = lazy<ComponentType>(async () => ({ default: (await load())[name] }))
  return function LazyPage() {
    return (
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <Page />
      </Suspense>
    )
  }
}

const AcceptInvite = lazyPage(() => import('./pages/AcceptInvite'), 'AcceptInvite')
const Terms = lazyPage(() => import('./pages/Legal'), 'Terms')
const Privacy = lazyPage(() => import('./pages/Legal'), 'Privacy')
const LicenceTerms = lazyPage(() => import('./pages/LicenceTerms'), 'LicenceTerms')
const Forgot = lazyPage(() => import('./pages/Password'), 'Forgot')
const Reset = lazyPage(() => import('./pages/Password'), 'Reset')
const Buyers = lazyPage(() => import('./pages/app/Buyers'), 'Buyers')
const BuyerPage = lazyPage(() => import('./pages/app/Buyers'), 'BuyerPage')
const Inventory = lazyPage(() => import('./pages/app/Inventory'), 'Inventory')
const Giveaways = lazyPage(() => import('./pages/app/Giveaways'), 'Giveaways')
const Orders = lazyPage(() => import('./pages/app/Orders'), 'Orders')
const OrderDetail = lazyPage(() => import('./pages/app/Orders'), 'OrderDetail')
const PickList = lazyPage(() => import('./pages/app/Orders'), 'PickList')
const OrderSlips = lazyPage(() => import('./pages/app/OrderSlips'), 'OrderSlips')
const Products = lazyPage(() => import('./pages/app/Products'), 'Products')
const Reports = lazyPage(() => import('./pages/app/Reports'), 'Reports')
const Settings = lazyPage(() => import('./pages/app/Settings'), 'Settings')
const Shipping = lazyPage(() => import('./pages/app/Shipping'), 'Shipping')
const Review = lazyPage(() => import('./pages/app/Review'), 'Review')
const ShowDetail = lazyPage(() => import('./pages/app/ShowDetail'), 'ShowDetail')
const Team = lazyPage(() => import('./pages/app/Team'), 'Team')
const WhatnotListings = lazyPage(() => import('./pages/app/WhatnotListings'), 'WhatnotListings')

function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="public">
      <header className="app-header">
        <h1>
          <Link to="/" className="brand-link">
            <img src="/logo.png" alt="" className="brand-logo" />
            Sales Ops
          </Link>
        </h1>
      </header>
      <main className="app-main">{children}</main>
      <Footer />
    </div>
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
            <Route path="/terms" element={<Terms />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/license" element={<LicenceTerms />} />
          </Route>
          <Route path="/app" element={<RequireAuth />}>
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="shows" element={<Shows />} />
              <Route path="shows/:id" element={<ShowDetail />} />
              <Route path="orders" element={<Orders />} />
              <Route path="orders/giveaways" element={<Giveaways />} />
              <Route path="orders/pick-list/:showId" element={<PickList />} />
              <Route path="orders/slips/:showId" element={<OrderSlips />} />
              <Route path="orders/shipping/:showId" element={<Shipping />} />
              <Route path="orders/:id" element={<OrderDetail />} />
              <Route path="buyers" element={<Buyers />} />
              <Route path="buyers/:name" element={<BuyerPage />} />
              <Route path="inventory" element={<Inventory />} />
              <Route path="inventory/whatnot" element={<WhatnotListings />} />
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
