# Sales Ops on Cloudflare

A second, **separate** deployment of Sales Ops next to Render. It has its own
database, its own shop owner and setup token, and its own paired laptops.
Nothing here touches the Render services or their database, and `render.yaml`
doesn't use any of these files.

## Topology

```
browser / Chat Scraper ──► Worker "sales-ops" (cloudflare/src/index.ts)
                              │  one Durable Object, one container (max_instances 1)
                              ▼
                      container (Dockerfile, instance_type basic: ¼ vCPU, 1 GiB)
                        cloudflare/entrypoint.sh:
                          flask db upgrade
                          python -m app.worker   (jobs table)
                          gunicorn :8080         (portal + API + web/dist + extension zip)
                              │ TCP 5432, TLS
                              ▼
                      Neon Postgres (its own project, not the Render DB)
```

- **Same origin as on Render.** The Worker passes every request through to
  Flask, so the `wn_session` cookie, the CSRF header and the SPA routes all
  behave the same way.
- **Client address.** Flask trusts one `X-Forwarded-For` hop (ProxyFix). The
  Worker deletes any forwarding headers the client sent and sets
  `X-Forwarded-For` to `CF-Connecting-IP` and `X-Forwarded-Proto` to the real
  scheme. That address is what rate limits key on. It also strips
  `cf-container-target-port`, which would otherwise let a caller pick a port
  inside the container.
- **Sleep.** After 30 minutes with no requests the container gets SIGTERM.
  The entrypoint stops gunicorn and lets the job worker finish its current
  job, then the container sleeps and costs nothing. The next request starts it
  again: about 5 s locally, a little more on Cloudflare (migrations run on
  every start and are a no-op when already applied). The Worker waits up to
  2 minutes for the port, then answers 503 with `Retry-After`.
- **Jobs run only while the container is awake.** Jobs are created by
  requests (ingest, imports, reports, AI review), so the container is awake
  when they're queued and stays up 30 minutes after the last request. Stale
  jobs are re-queued and the retention sweep runs when the worker starts.
  There's no cron: a cron that woke the container every few minutes would
  keep it awake all the time and bill for it.
- **The disk is ephemeral.** The app keeps no files on disk: everything is in
  Postgres, and reports are built per request.
- If either process dies, the entrypoint stops the other and exits, and
  Cloudflare starts a fresh container.

## Cost (from developers.cloudflare.com/containers/pricing, read 2026-11-05)

- Workers Paid plan: $5/month. This includes 25 GiB-hours of memory,
  375 vCPU-minutes and 200 GB-hours of disk.
- Beyond that you pay per 10 ms while the container runs: $0.0000025 per
  GiB-second, $0.000020 per active vCPU-second, $0.00000007 per GB-second of
  disk.
- `basic` (1 GiB, 4 GB disk) running 8 h a day is about 240 GiB-hours/month,
  roughly $2 over the included amount. If it ran 24/7 that would be about $6.
- Neon free tier for the database, or a paid Neon plan once real data lives
  there.

## First deploy (each step needs Javier)

1. **Neon.** Create a project named "sales-ops-cloudflare" in a US region
   (US East is closest to the shop). Copy the **direct** connection string,
   not the `-pooler` one. It looks like `postgresql://…@ep-….neon.tech/neondb?sslmode=require`.
   The app keeps its own small pool (5 + 5 per process), so the direct
   endpoint is enough, and it avoids PgBouncer surprises during migrations.
2. **Cloudflare account** on the Workers Paid plan (Containers need it).
3. **Docker Desktop running** on the Mac. `wrangler deploy` builds the image
   locally (linux/amd64) and pushes it to Cloudflare's registry.
4. Log in and install:
   ```bash
   cd cloudflare && npm ci && npx wrangler login
   ```
5. **Secrets.** Each command prompts for the value, so nothing lands in shell
   history or the repo:
   ```bash
   npx wrangler secret put DATABASE_URL      # the Neon direct string
   npx wrangler secret put SECRET_KEY        # paste the output of: openssl rand -hex 32
   npx wrangler secret put ADMIN_EMAIL       # the shop owner's email
   npx wrangler secret put SETUP_TOKEN       # paste the output of: openssl rand -hex 16 (keep it for step 8)
   ```
   The first `secret put` may offer to create the Worker. Say yes.
6. **Deploy:** `npm run deploy` (inside `cloudflare/`). It prints the
   address, `https://sales-ops.<your-subdomain>.workers.dev`. The first
   container rollout can take a few minutes. Until then the Worker answers
   503.
7. **APP_URL.** The extension download and email links need it. Production
   refuses to guess it from the Host header.
   ```bash
   npx wrangler secret put APP_URL           # https://sales-ops.<your-subdomain>.workers.dev
   ```
   A secret change takes effect the next time the container starts. Run
   `npm run deploy` again to roll it now.
8. **Smoke and claim the shop.** From the repo root:
   ```bash
   make smoke BASE_URL=https://sales-ops.<your-subdomain>.workers.dev
   ```
   Then open `/signup` and use ADMIN_EMAIL plus the SETUP_TOKEN from step 5.
9. Optional, when ready (same `secret put`): `MAILGUN_API_KEY`,
   `MAILGUN_DOMAIN`, `EMAIL_FROM`, `AI_PROVIDER` + `ANTHROPIC_API_KEY`
   (or the `JEV_*` set), `LOG_LEVEL`, `IT_SUPPORT` / `IT_SUPPORT_PASSWORD`.
   The full list the container receives is `PASSED_ENV` in
   `cloudflare/src/proxy.ts`. A secret that isn't on that list never reaches
   Flask. `APP_ENV` is fixed to `production` in the image.
10. **Custom domain (optional).** Workers → sales-ops → Settings → Domains &
    Routes. Then update `APP_URL` to that domain and download the extension
    again on each laptop.

## Day to day

- **Deploys are manual.** Merging to `main` redeploys Render, not this.
  From an up-to-date `main`: `cd cloudflare && npm run deploy`.
- Logs: `npm run tail`, or Workers → sales-ops → Logs (observability is on).
  The container's stdout (gunicorn access log, worker) shows there too.
- Instances and images: `npx wrangler containers list`,
  `npx wrangler containers images list`.
- **Migrations** run at container start, before gunicorn and the job worker.
  The frozen-migration rule applies here too: once a revision has run on this
  Neon database, it is applied there.
- `make check` covers the Worker (`cloudflare/`: typecheck + vitest). CI also
  builds the image.

## What was verified locally (2026-11-05)

- The image builds for linux/amd64 and contains no `.env`.
- Against a throwaway Postgres 16 container, it:
  - migrated from scratch;
  - started gunicorn and the job worker;
  - passed `make smoke`;
  - completed sign-up with ADMIN_EMAIL + SETUP_TOKEN;
  - served `/api/extension/download` with APP_URL baked in;
  - set a `Secure` session cookie and HSTS behind `X-Forwarded-Proto: https`.
- On SIGTERM both processes stopped cleanly ("worker stopped"). When the job
  worker was SIGKILLed, the container exited.
- `wrangler deploy --dry-run` passed: config valid, image built, binding
  wired.
- `wrangler dev`, the Worker and container running locally under Docker:
  - Secrets reached Flask.
  - Health returned 200 after a ~5 s cold start.
  - A spoofed `X-Forwarded-For` was replaced: the rate-limit key was the
    address Cloudflare reported.
  - `cf-container-target-port` was stripped.

## Not verified yet (only a real deploy can show these)

- **TCP from a Cloudflare container to Neon on 5432.** The docs say internet
  access is on by default and that only `enableInternet = false` limits ports.
  The first `make smoke` (`"db":true`) confirms it.
- **The client address on Cloudflare itself.** Locally the Worker's
  `X-Forwarded-For` reached Flask unchanged. If Cloudflare's container network
  appended its own hop, rate limits would key on that hop instead. After the
  first deploy, sign in from two networks, check `select key from rate_limits`
  on Neon, and confirm the keys are real client IPs.
- Cold start time on a `basic` instance in Cloudflare.
- **Rollouts.** During a deploy the old and new container can overlap
  briefly, and both run `flask db upgrade`. Fine while there's no new
  revision. For a release that adds one, deploy when no show is live.
