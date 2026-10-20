import os

import click
from flask import Flask
from sqlalchemy import select

from ..config import db_host
from ..db import db
from ..models import Organization
from ..services.devices import create_device


def _org(name: str) -> Organization:
    slug = name.lower().strip().replace(" ", "-")
    org = db.session.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        org = Organization(name=name.strip(), slug=slug)
        db.session.add(org)
        db.session.flush()
    return org


def register_cli(app: Flask) -> None:
    @app.cli.group()
    def devices():
        """Extension device keys."""

    @devices.command("create")
    @click.option("--org", required=True, help="Organization name (created if missing).")
    @click.option("--name", required=True, help="Device label, e.g. 'Front laptop'.")
    def devices_create(org: str, name: str):
        click.echo(f"database: {db_host(app.config['SQLALCHEMY_DATABASE_URI'])}")
        device, key = create_device(_org(org), name)
        db.session.commit()
        click.echo(f"device #{device.id} '{device.name}' for '{device.organization.name}'")
        click.echo("API key (shown once, paste it into the extension options):")
        click.echo(key)

    @app.cli.group()
    def users():
        """Accounts."""

    @users.command("create-owner")
    @click.option("--org", required=True, help="Organization name (created if missing).")
    @click.option("--email", required=True)
    @click.option("--name", required=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def users_create_owner(org: str, email: str, name: str, password: str):
        """First owner of a shop (for production, where sign-ups are closed)."""
        from ..models import Role, User
        from ..services import accounts

        click.echo(f"database: {db_host(app.config['SQLALCHEMY_DATABASE_URI'])}")
        organization = _org(org)
        email = accounts.normalise_email(email)
        accounts.check_password_strength(password)
        if db.session.scalar(select(User.id).where(User.email == email)):
            raise click.ClickException("that email already has an account")
        import secrets

        user = User(
            organization=organization,
            email=email,
            name=name,
            password_hash=accounts._ph.hash(password),
            role=Role.owner,
            session_token=secrets.token_hex(16),
        )
        db.session.add(user)
        db.session.commit()
        click.echo(f"owner {email} created for '{organization.name}'")

    @app.cli.command("e2e-seed")
    def e2e_seed():
        """Test only: a device whose key is $E2E_DEVICE_KEY. Refused in production."""
        if app.config.get("APP_ENV") == "production":
            raise click.ClickException("refused in production")
        key = os.environ.get("E2E_DEVICE_KEY")
        if not key:
            raise click.ClickException("E2E_DEVICE_KEY is not set")
        from ..services.devices import device_for_key

        if device_for_key(key) is None:
            create_device(_org("E2E Shop"), "e2e device", key=key)
            db.session.commit()
        click.echo("e2e device ready")
