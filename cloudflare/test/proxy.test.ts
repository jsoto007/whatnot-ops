import { describe, expect, it } from "vitest";
import { PASSED_ENV, containerEnv, forContainer } from "../src/proxy";

describe("forContainer", () => {
  it("replaces viewer-supplied forwarding headers with what Cloudflare saw", () => {
    const req = new Request("https://sales-ops.example.workers.dev/api/health", {
      headers: {
        "cf-connecting-ip": "203.0.113.7",
        "x-forwarded-for": "1.2.3.4, 5.6.7.8",
        "x-forwarded-proto": "http",
        "x-forwarded-host": "evil.example",
        forwarded: "for=1.2.3.4",
        "x-real-ip": "1.2.3.4",
        cookie: "wn_session=abc",
      },
    });
    const out = forContainer(req);
    expect(out.headers.get("x-forwarded-for")).toBe("203.0.113.7");
    expect(out.headers.get("x-forwarded-proto")).toBe("https");
    expect(out.headers.has("x-forwarded-host")).toBe(false);
    expect(out.headers.has("forwarded")).toBe(false);
    expect(out.headers.has("x-real-ip")).toBe(false);
    expect(out.headers.get("cookie")).toBe("wn_session=abc");
    expect(out.url).toBe(req.url);
  });

  it("drops the header that would pick another container port", () => {
    const req = new Request("https://x.example/", { headers: { "cf-container-target-port": "5432" } });
    expect(forContainer(req).headers.has("cf-container-target-port")).toBe(false);
  });

  it("sets no client address when Cloudflare gave none", () => {
    const req = new Request("https://x.example/", { headers: { "x-forwarded-for": "9.9.9.9" } });
    expect(forContainer(req).headers.has("x-forwarded-for")).toBe(false);
  });

  it("keeps the method and body of a POST", async () => {
    const req = new Request("https://x.example/api/ingest", {
      method: "POST",
      body: JSON.stringify({ ok: true }),
      headers: { "content-type": "application/json", "x-csrf-token": "t" },
    });
    const out = forContainer(req);
    expect(out.method).toBe("POST");
    expect(out.headers.get("x-csrf-token")).toBe("t");
    expect(await out.json()).toEqual({ ok: true });
  });
});

describe("containerEnv", () => {
  it("passes only the listed, non-empty settings", () => {
    const env = {
      DATABASE_URL: "postgresql://u:p@db.example/x",
      SECRET_KEY: "s",
      ADMIN_EMAIL: "",
      APP_ENV: "development",
      SALES_OPS: {},
    } as Record<string, unknown>;
    expect(containerEnv(env)).toEqual({ DATABASE_URL: "postgresql://u:p@db.example/x", SECRET_KEY: "s" });
  });

  it("never lets APP_ENV through: the image pins production", () => {
    expect(PASSED_ENV).not.toContain("APP_ENV" as never);
  });
});
