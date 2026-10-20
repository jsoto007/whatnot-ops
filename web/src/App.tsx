import { BrowserRouter, Link, Route, Routes } from 'react-router'
import { Home } from './pages/Home'

export function App() {
  return (
    <BrowserRouter>
      <header className="app-header">
        <h1>
          <Link to="/" style={{ color: 'inherit', textDecoration: 'none' }}>
            Whatnot Ops
          </Link>
        </h1>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="*" element={<p>Not found.</p>} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
