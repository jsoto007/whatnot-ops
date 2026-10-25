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
    from .seed_demo import register as register_seed_demo

    register_seed_demo(app)

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
            password_hash=accounts.hash_password(password),
            role=Role.owner,
            session_token=secrets.token_hex(16),
        )
        db.session.add(user)
        db.session.commit()
        click.echo(f"owner {email} created for '{organization.name}'")

    @app.cli.command("ensure-support")
    def ensure_support():
        """Create or repair the IT_SUPPORT account (run at every start, before gunicorn)."""
        from ..services import support
        from ..services.accounts import AccountError

        click.echo(f"database: {db_host(app.config['SQLALCHEMY_DATABASE_URI'])}")
        if not (support.support_email() or support.support_password()):
            click.echo("IT_SUPPORT not set; no support account")
            return
        try:
            user = support.ensure_support_account()
        except AccountError as e:
            raise click.ClickException(f"IT_SUPPORT is misconfigured: {e}") from None
        if user is None:
            click.echo("support account skipped (see the log): no shop yet, or a variable missing")
        else:
            click.echo(f"support account {user.email} is an owner of '{user.organization.name}'")

    @app.cli.command("ai-eval")
    def ai_eval():
        """Replay the rules corpus through the configured AI provider and report agreement."""
        import json
        from pathlib import Path

        from ..services.ai import get_provider
        from ..services.supervisor import CLASSIFY_SCHEMA

        provider = get_provider()
        if provider is None:
            raise click.ClickException("AI_PROVIDER is not set")
        corpus = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "chat_corpus.jsonl"
        rows = [json.loads(line) for line in corpus.read_text().splitlines() if line.strip()]
        agree = total = 0
        for r in rows:
            expected = {
                "sale": "sale",
                "cancel": "cancel",
                "payment_failed": "payment_failed",
                "gift": "gift",
            }.get(r["kind"], "chatter")
            d = provider.decide(
                task=(
                    "classify_line: Decide what this chat line means for the shop's sales. "
                    "The host is host_shop."
                ),
                input=json.dumps({"line": {"user": r["u"], "text": r["b"]}}),
                schema=CLASSIFY_SCHEMA,
            )
            got = d.fields.get("kind")
            total += 1
            agree += got == expected
            mark = "ok " if got == expected else "MISS"
            click.echo(f"{mark} {r['b']!r}: expected {expected}, got {got} ({d.confidence:.2f})")
        click.echo(f"agreement: {agree}/{total} = {agree / total:.0%} on the SYNTHETIC corpus")

    @app.cli.command("e2e-seed")
    def e2e_seed():
        """Test only: a device whose key is $E2E_DEVICE_KEY (and a pairing code
        $E2E_PAIRING_CODE when set). Refused in production."""
        if app.config.get("APP_ENV") == "production":
            raise click.ClickException("refused in production")
        key = os.environ.get("E2E_DEVICE_KEY")
        if not key:
            raise click.ClickException("E2E_DEVICE_KEY is not set")
        from ..services.devices import device_for_key

        if device_for_key(key) is None:
            create_device(_org("E2E Shop"), "e2e device", key=key)
            db.session.commit()
        # E2E Mug starts every run with 4 available, so the scraper's 3/2/1/sold-out
        # alerts can be counted down. Topped up through the ledger, like a delivery.
        from ..models import MovementKind, Sku
        from ..services import catalog, inventory

        org = _org("E2E Shop")
        mug = db.session.scalar(
            select(Sku).where(Sku.organization_id == org.id, Sku.code == "E2E-MUG")
        )
        if mug is None:
            mug = catalog.create_product(org.id, "E2E Mug", skus=[{"code": "E2E-MUG"}]).skus[0]
        if mug.available < 4:
            inventory.move(mug.id, MovementKind.restock, 4 - mug.available, source_type="e2e")
        db.session.commit()
        code = os.environ.get("E2E_PAIRING_CODE")
        if code:
            # A fresh, unused pairing code with a known value, re-created on every run.
            from datetime import UTC, datetime, timedelta

            from ..models import DevicePairing
            from ..services.devices import hash_key, normalise_code

            h = hash_key(normalise_code(code))
            db.session.execute(db.delete(DevicePairing).where(DevicePairing.code_hash == h))
            now = datetime.now(UTC)
            db.session.add(
                DevicePairing(
                    organization_id=_org("E2E Shop").id,
                    name="Paired laptop",
                    code_hash=h,
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )
            db.session.commit()
        click.echo("e2e device ready")
