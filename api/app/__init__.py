import logging
import os

from flask import Flask

from .cli import register_cli
from .config import Config, db_host
from .db import db, migrate
from .routes.audit import bp as audit_bp
from .routes.auth import bp as auth_bp
from .routes.devices import bp as devices_bp
from .routes.health import bp as health_bp
from .routes.ingest import bp as ingest_bp
from .routes.shows import bp as shows_bp
from .routes.team import bp as team_bp
from .spa import register_spa

log = logging.getLogger(__name__)


def create_app(config: Config | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))

    from . import models  # noqa: F401 - register tables with SQLAlchemy/Alembic

    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(ingest_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(team_bp, url_prefix="/api/team")
    app.register_blueprint(devices_bp, url_prefix="/api/devices")
    app.register_blueprint(audit_bp, url_prefix="/api/audit")
    app.register_blueprint(shows_bp, url_prefix="/api/shows")
    register_spa(app)
    register_cli(app)

    log.info("database: %s", db_host(config.SQLALCHEMY_DATABASE_URI))
    return app
