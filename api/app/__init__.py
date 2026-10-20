import logging
import os

from flask import Flask

from .cli import register_cli
from .config import Config, db_host
from .db import db, migrate
from .routes.health import bp as health_bp
from .routes.ingest import bp as ingest_bp
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
    register_spa(app)
    register_cli(app)

    log.info("database: %s", db_host(config.SQLALCHEMY_DATABASE_URI))
    return app
