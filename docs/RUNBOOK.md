# Runbook

How Whatnot Ops is deployed and operated. Nothing here runs on its own: every
dashboard action, deploy and database write outside a laptop needs Javier's OK.

## Topology (Render Blueprint, `render.yaml`)

| piece | what |
|---|---|
| `whatnot-ops` (web, Python) | builds `web/dist`, runs `flask db upgrade`, serves the API and the SPA with gunicorn |
| `whatnot-ops-worker` (worker, Python) | `python -m app.worker`: parse shows, AI supervision; polls the `jobs` table |
| `whatnot-ops-db` (Postgres, basic-256mb) | the only state |

The web service never runs jobs in production (`JOBS_INLINE` must stay
unset); the worker does. Both read the same `DATABASE_URL`.

## First deploy

1. Render → New → Blueprint → this repo, branch `main`. Render creates the
   database and both services and applies migrations on start.
2. In the web service's environment add `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`,
   `EMAIL_FROM` (and later `APP_URL` once a domain is attached). Without
   Mailgun, invites and resets are only logged.
3. Optional AI: `AI_PROVIDER=claude` + `ANTHROPIC_API_KEY` (or `jev` +
   `JEV_API_KEY`, `JEV_API_URL`) on **both** services. The worker makes the
   calls. `AI_PROVIDER=fake` is refused in production.
4. Smoke test (also `make smoke BASE_URL=https://…`):
   `GET /api/health` → `{"status":"ok","db":true}`; `/` serves the sign-in
   page; `/some/route` falls back to the SPA; `/api/nope` is 404.
5. First owner: open `/signup` (the very first account is always allowed), or
   from the web service's Shell:
   `cd api && flask --app wsgi.py users create-owner --org "Shop" --email you@… --name "You"`.
6. Settings → set the shop's Whatnot username (host) and create a laptop key;
   paste it with the service URL into the extension's Settings.

## Day to day

- **A show is live**: the extension uploads every few seconds; the worker
  parses within ~3 s of each batch; the dashboard's live pill links to it.
- **Review queue** (owners/managers): unknown items, stock conflicts,
  ambiguous cancellations, AI proposals. Assigning a SKU with "remember" teaches
  the parser the chat name.
- **After the show**: Seller Hub → export orders → Payments tab on the show →
  Preview → Apply. Paid rows take the stock; refunded rows release it.
  Orders → To fulfil → Pick list → pick / pack / ship.
- **Reports**: one show or a date range as `.xlsx`.

## Operations

- **Logs**: web and worker logs in Render. The worker logs each failed job
  with its attempt; after 5 attempts a job is `failed` in the `jobs` table.
  Re-queue by setting `status='queued'` (needs Javier's OK on prod).
- **Re-parse a show**: show page → Parse now (re-evaluates only lines that led
  nowhere; existing sales are never rewritten).
- **AI evaluation**: `flask --app wsgi.py ai-eval` replays the synthetic
  corpus through the configured provider.
- **Backups**: Render Postgres daily backups (paid plan). Restore = new
  database from a backup, repoint `DATABASE_URL`, redeploy.
- **Secrets**: only in Render env vars. A leaked key is rotated, never just
  removed from history. `SECRET_KEY` rotation signs everyone out.
- **Billing**: a suspended workspace shows up as failed deploys and 502s;
  check billing before debugging.

## Migrations

`flask db migrate` prints the generated file — rename *that* file to the
next `NNNN_name.py`. A revision that has run on prod is frozen; new changes
are new revisions. Downgrades that drop tables using Postgres enums must drop
the enum types (see 0004, 0007).

## Extension for the team

```bash
cd extension && DEFAULT_SERVER_URL=https://ops.example.com npm run package
```

makes `chat-scraper-<version>.zip` from `dist/` with the server URL baked in
as the default (each laptop still needs its own key). Load unpacked, or
publish privately through the Chrome Web Store later.
