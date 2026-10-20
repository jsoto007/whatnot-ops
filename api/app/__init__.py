import logging
import os

from flask import Flask

from .config import Config, db_host
from .db import db, migrate
from .routes.health import bp as health_bp
from .spa import register_spa

log = logging.getLogger(__name__)


def create_app(config: Config | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))

    app.register_blueprint(health_bp, url_prefix="/api")
    register_spa(app)

    log.info("database: %s", db_host(config.SQLALCHEMY_DATABASE_URI))
    return app
