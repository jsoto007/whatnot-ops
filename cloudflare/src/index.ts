/**
 * Cloudflare front door for Sales Ops: every request goes to one container
 * (the Dockerfile at the repo root) that runs gunicorn and the job worker.
 *
 * This is a separate deployment from Render, with its own database and secrets.
 * See docs/CLOUDFLARE.md.
 */
import { Container, getContainer } from "@cloudflare/containers";
import { PORT, containerEnv, forContainer, type PassedEnv } from "./proxy";

export interface Env extends PassedEnv {
  SALES_OPS: DurableObjectNamespace<SalesOps>;
}

export class SalesOps extends Container<Env> {
  defaultPort = PORT;
  // Asleep, the container costs nothing; awake it runs the job worker too.
  // Jobs come from requests (ingest, imports, reports), so staying up for a
  // while after the last one lets queued parsing and AI work finish.
  sleepAfter = "30m";
  // Neon (Postgres over TCP 5432), Mailgun and the AI vendor are on the internet.
  enableInternet = true;

  constructor(ctx: DurableObjectState<object>, env: Env) {
    super(ctx, env, { envVars: containerEnv(env) });
  }

  override async fetch(request: Request): Promise<Response> {
    // A cold start migrates before gunicorn listens; the library's default
    // wait for the port (20 s) is too short for that on a small instance.
    if (!this.ctx.container?.running) {
      try {
        await this.startAndWaitForPorts({
          ports: PORT,
          cancellationOptions: { portReadyTimeoutMS: 120_000, abort: request.signal },
        });
      } catch (e) {
        console.error("container did not start", e);
        return new Response("Sales Ops is starting up. Try again in a minute.", {
          status: 503,
          headers: { "retry-after": "30", "content-type": "text/plain; charset=utf-8" },
        });
      }
    }
    return this.containerFetch(request, PORT);
  }

  override onStop({ exitCode, reason }: { exitCode: number; reason: string }) {
    console.log(`container stopped: exit ${exitCode}, ${reason}`);
  }

  override onError(error: unknown) {
    console.error("container error", error);
    throw error;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // One instance: the jobs queue and the migrations assume a single deployment.
    return getContainer(env.SALES_OPS).fetch(forContainer(request));
  },
} satisfies ExportedHandler<Env>;
