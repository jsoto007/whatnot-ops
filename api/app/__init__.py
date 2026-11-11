import json
import logging
import os
import traceback

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .cli import register_cli
from .config import Config, db_host
from .db import db, migrate
from .logsafe import error_line
from .routes.audit import bp as audit_bp
from .routes.auth import bp as auth_bp
from .routes.catalog import bp as catalog_bp
from .routes.devices import bp as devices_bp
from .routes.extension import bp as extension_bp
from .routes.health import bp as health_bp
from .routes.ingest import bp as ingest_bp
from .routes.orders import bp as orders_bp
from .routes.pair import bp as pair_bp
from .routes.reports import bp as reports_bp
from .routes.review import bp as review_bp
from .routes.shows import bp as shows_bp
from .routes.team import bp as team_bp
from .routes.webhooks import bp as webhooks_bp
from .routes.whatnot import bp as whatnot_bp
from .spa import register_spa

log = logging.getLogger(__name__)

# The SPA has no inline script; fonts come from Google Fonts (web/index.html).
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def _security_headers(app: Flask) -> None:
    production = app.config.get("APP_ENV") == "production"

    @app.after_request
    def headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if production:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/api/"):
            # Shop data (buyers, money) never sits in a shared or browser cache.
            h["Cache-Control"] = "no-store"
        return resp

    from .services.ratelimit import RateLimited

    @app.errorhandler(RateLimited)
    def rate_limited(e):
        from .db import db

        db.session.rollback()
        resp = jsonify({"error": str(e)})
        if e.retry_after:
            resp.headers["Retry-After"] = str(e.retry_after)
        return resp, 429

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "request body too large"}), 413

    @app.errorhandler(Exception)
    def unhandled(e):
        """Anything a route did not turn into a response: roll back, log, answer JSON.
        HTTP errors (404, 405, ...) keep their own status."""
        from werkzeug.exceptions import HTTPException

        from .db import db

        if isinstance(e, HTTPException):
            if not request.path.startswith("/api/"):
                return e
            resp = e.get_response()  # keeps Allow on 405, Location on redirects, ...
            resp.set_data(json.dumps({"error": e.description or e.name}))
            resp.content_type = "application/json"
            return resp
        db.session.rollback()
        if isinstance(e, RecursionError):  # most likely a JSON body nested thousands deep
            log.warning("RecursionError on %s %s", request.method, request.path, exc_info=e)
            return jsonify({"error": "request body is nested too deeply"}), 400
        from sqlalchemy.exc import DBAPIError

        if isinstance(e, DBAPIError):
            # Postgres quotes the failing row in its DETAIL (password hashes, emails), and
            # the exception's text carries it: log the stack and the primary message only.
            log.error(
                "unhandled database error on %s %s: %s\n%s",
                request.method,
                request.path,
                error_line(e),
                "".join(traceback.format_tb(e.__traceback__)),
            )
        else:
            log.exception("unhandled error on %s %s", request.method, request.path)
        return jsonify({"error": "something went wrong on our side"}), 500


def _log_first_client_address(app: Flask) -> None:
    """One WARNING line per process (the web process configures no logging, so INFO never
    shows), on the first request that is not Render's health check: the address the rate
    limits will use, next to the one Cloudflare reports. "match" means PROXY_HOPS is right."""
    seen = []

    @app.before_request
    def log_client_address():
        if seen or request.path == "/api/health":
            return
        seen.append(True)
        entries = len(
            [p for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
        )
        cloudflare = request.headers.get("CF-Connecting-IP", "")
        log.warning(
            "client address check: using %s, Cloudflare saw %s (%s; %d X-Forwarded-For "
            "entries, PROXY_HOPS=%s)",
            request.remote_addr,
            cloudflare or "-",
            "match" if cloudflare == request.remote_addr else "MISMATCH",
            entries,
            app.config.get("PROXY_HOPS"),
        )


def create_app(config: Config | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config)
    if config.APP_ENV == "production":
        # Cloudflare sits in front of Render (`server: cloudflare`, seen 2026-10-25), so a
        # request arrives with X-Forwarded-For "<client>, <cloudflare edge>": Cloudflare
        # appends the address it saw, Render appends Cloudflare's, and whatever a client
        # sent itself stays further left. The client is therefore the second entry from
        # the right (PROXY_HOPS). With one hop every visitor behind the same edge shared
        # one per-address rate-limit bucket. The scheme comes from the last hop.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.PROXY_HOPS, x_proto=1)
        _log_first_client_address(app)
    _security_headers(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))

    from . import models  # noqa: F401 - register tables with SQLAlchemy/Alembic
    from .services import pipeline, supervisor  # noqa: F401 - registers job handlers
    from .services.whatnot import webhooks  # noqa: F401 - registers the whatnot_event job

    if config.WHATNOT_FAKE:
        # Local work and the browser tests: an in-memory Whatnot instead of the live shop.
        from .services.whatnot import fake

        app.extensions["whatnot_fake"] = shop = fake.FakeWhatnot()
        app.config.update(WHATNOT_TRANSPORT=shop.transport, WHATNOT_API_TOKEN=fake.TOKEN)

    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(ingest_bp, url_prefix="/api")
    app.register_blueprint(pair_bp, url_prefix="/api")
    app.register_blueprint(extension_bp, url_prefix="/api/extension")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(team_bp, url_prefix="/api/team")
    app.register_blueprint(devices_bp, url_prefix="/api/devices")
    app.register_blueprint(audit_bp, url_prefix="/api/audit")
    app.register_blueprint(shows_bp, url_prefix="/api/shows")
    app.register_blueprint(catalog_bp, url_prefix="/api")
    app.register_blueprint(review_bp, url_prefix="/api/review")
    app.register_blueprint(orders_bp, url_prefix="/api/orders")
    app.register_blueprint(reports_bp, url_prefix="/api")
    app.register_blueprint(whatnot_bp, url_prefix="/api/whatnot")
    app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    register_spa(app)
    register_cli(app)

    log.info("database: %s", db_host(config.SQLALCHEMY_DATABASE_URI))
    return app
