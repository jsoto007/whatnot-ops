# Whatnot Ops

Live-show operations for a Whatnot seller: a Chrome extension captures show
chat, a Flask API turns it into sales, inventory movements and orders, and a
React dashboard gives the fulfillment team everything at a glance.

See `docs/PLAN.md` for the phased build plan and `CLAUDE.md` for working
rules. Phase 0 (this scaffold) ships an empty app end to end: API health
route, SPA served by Flask, tests and CI.

## Layout

```
api/         Flask + SQLAlchemy + Alembic (REST API, jobs, exports)
web/         React 19 + Vite + TypeScript SPA
extension/   Chrome MV3 extension (Chat Scraper)
shared/      JSON Schema contract between extension and API
docs/        plan, runbook
```

## Run locally

```bash
cp .env.example .env            # local Postgres.app, see values inside
make db-create                  # creates whatnot_ops and whatnot_ops_test
make install                    # python venv + npm install
make api                        # Flask on http://localhost:5000
make web                        # Vite on http://localhost:5173 (proxies /api)
make check                      # lint + typecheck + unit tests, both sides
make e2e                        # production build served by Flask + Playwright
```
