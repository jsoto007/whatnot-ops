import logging
import os

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .cli import register_cli
from .config import Config, db_host
from .db import db, migrate
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
        return jsonify({"error": str(e)}), 429

    @app.errorhandler(RecursionError)
    def too_deep(_e):
        # JSON nested thousands deep: refused, not a 500.
        return jsonify({"error": "request body is nested too deeply"}), 400

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "request body too large"}), 413


def create_app(config: Config | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config)
    if config.APP_ENV == "production":
        # Render terminates TLS in front of gunicorn: trust exactly one proxy hop for the
        # client address and scheme (rate limits, Secure cookies, request.is_secure).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    _security_headers(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))

    from . import models  # noqa: F401 - register tables with SQLAlchemy/Alembic
    from .services import pipeline, supervisor  # noqa: F401 - registers job handlers

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
    register_spa(app)
    register_cli(app)

    log.info("database: %s", db_host(config.SQLALCHEMY_DATABASE_URI))
    return app
