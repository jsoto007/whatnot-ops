/** Pure helpers for the Worker → container hop (no Workers runtime imports, so tests run in Node). */

export const PORT = 8080;

/**
 * Settings the Flask app reads (api/app/config.py and friends). Set them with
 * `wrangler secret put NAME`; a name that is not set is simply not passed.
 * APP_ENV is fixed to production in the image and cannot be overridden here.
 */
export const PASSED_ENV = [
  "DATABASE_URL",
  "SECRET_KEY",
  "ADMIN_EMAIL",
  "SETUP_TOKEN",
  "APP_URL",
  "MAILGUN_API_KEY",
  "MAILGUN_DOMAIN",
  "MAILGUN_API_BASE",
  "EMAIL_FROM",
  "AI_PROVIDER",
  "ANTHROPIC_API_KEY",
  "CLAUDE_MODEL",
  "JEV_API_KEY",
  "JEV_API_URL",
  "JEV_AUTH_HEADER",
  "JEV_MODEL",
  "IT_SUPPORT",
  "IT_SUPPORT_PASSWORD",
  "LOG_LEVEL",
  "WORKER_POLL_SECONDS",
  "WEB_WORKERS",
] as const;

type PassedName = (typeof PASSED_ENV)[number];

export type PassedEnv = Partial<Record<PassedName, string>>;

export function containerEnv(env: PassedEnv): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of PASSED_ENV) {
    const value = env[name];
    if (typeof value === "string" && value !== "") out[name] = value;
  }
  return out;
}

/** Headers a viewer must never be able to set on the hop to the container. */
const HOP_HEADERS = [
  // @cloudflare/containers routes to this port when the header is present.
  "cf-container-target-port",
  "x-forwarded-for",
  "x-forwarded-proto",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-prefix",
  "x-real-ip",
  "forwarded",
];

/**
 * The request the container sees. Flask runs ProxyFix(x_for=1, x_proto=1) in
 * production, so it trusts exactly one X-Forwarded-For entry and the scheme:
 * we set both from what Cloudflare saw, replacing anything the client sent.
 * Rate limits key on that address (api/app/services/ratelimit.py).
 */
export function forContainer(request: Request): Request {
  const headers = new Headers(request.headers);
  for (const h of HOP_HEADERS) headers.delete(h);
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) headers.set("x-forwarded-for", ip);
  headers.set("x-forwarded-proto", new URL(request.url).protocol === "https:" ? "https" : "http");
  return new Request(request, { headers });
}
