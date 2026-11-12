# Notices

**Sales Ops** — Copyright © 2026 SotoDev. All rights reserved.

Sales Ops is proprietary software licensed only under [`LICENSE.md`](LICENSE.md).
Nothing in this file grants any right in Sales Ops itself.

## Third-party components

Sales Ops uses the open-source components below. Each remains the property of
its authors and is licensed under its own terms, which govern that component
and are not limited by `LICENSE.md`. The full licence text of each component
for everything delivered to browsers is in
[`THIRD_PARTY_LICENSES.txt`](THIRD_PARTY_LICENSES.txt); for server components
it is in each package.

This list covers the components Sales Ops runs on or delivers to users (the
hosted server, the web app sent to browsers, and the browser extension).
Development and test tools that are never delivered are not listed.

### Delivered to browsers (web app)

| Component | Licence |
|---|---|
| react, react-dom, scheduler | MIT |
| react-router | MIT |
| @tanstack/react-query, @tanstack/query-core | MIT |
| cookie | MIT |
| set-cookie-parser | MIT |

The web app loads the Inter and Inter Tight typefaces from Google Fonts; they
are licensed under the SIL Open Font License 1.1 and are not bundled.

### Delivered to browsers (Chat Scraper extension)

| Component | Licence |
|---|---|
| idb | ISC |

### Server (hosted by SotoDev, not distributed)

| Component | Licence |
|---|---|
| Flask | BSD-3-Clause |
| Flask-SQLAlchemy | BSD-3-Clause |
| Flask-Migrate | MIT |
| SQLAlchemy | MIT |
| psycopg, psycopg-binary | LGPL-3.0-only |
| pydantic | MIT |
| python-dotenv | BSD-3-Clause |
| gunicorn | MIT |
| jsonschema | MIT |
| argon2-cffi | MIT |
| requests | Apache-2.0 |
| anthropic | MIT |
| openpyxl | MIT |

Their own dependencies are installed by the package manager under their own
licences. psycopg is used unmodified as a separately installed library; its
source is available from the psycopg project.

## Trademarks

Whatnot, Google Chrome, Google Fonts, Render, Mailgun and Anthropic are
trademarks of their respective owners. Sales Ops and SotoDev are not
affiliated with, endorsed by or sponsored by any of them.
