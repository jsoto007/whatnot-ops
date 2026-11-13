"""The migrations folder has one head, so a plain `flask db upgrade` knows where to go.

Two pull requests that each add a revision on the same parent are green on their own.
Once both are on main, the web service's start command stops with "Multiple head
revisions are present" and no deploy can start (PRs 60 and 61, 2026-11-12, joined again
by 0027). This reads the migration files only, so it says what is wrong in one line
while every other test errors setting up its database.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(autouse=True)
def clean_tables():
    """In place of conftest's: nothing here needs the database, or its migrations to run."""
    yield


def test_migrations_have_one_head():
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    heads = sorted(ScriptDirectory.from_config(cfg).get_heads())
    assert len(heads) == 1, (
        f"{len(heads)} Alembic heads ({', '.join(heads)}): two revisions share a parent, so "
        "`flask db upgrade` refuses to run and a deploy cannot start. Rebase onto main and "
        "point your new revision at main's head; if both are already on main (frozen), join "
        'them with `flask db merge heads -m "<name>"`.'
    )
