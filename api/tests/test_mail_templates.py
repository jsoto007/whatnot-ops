"""The branded emails: both bodies carry the link, the logo comes from the app's
own URL, and nothing a person typed reaches the HTML unescaped."""

import re

from app.services import mail_templates

HOSTILE = '<img src=x onerror="alert(1)">'


def test_invite_html_and_text_both_carry_the_link_and_branding(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://ops.example.com")
    url = "https://ops.example.com/invite/abc_DEF-123"
    msg = mail_templates.invite(
        to="mia@shop.test",
        inviter_name="Olive Owner",
        organization="My Shop",
        role="manager",
        url=url,
        days=7,
    )
    assert msg.to == "mia@shop.test"
    assert msg.subject == "Olive Owner invited you to join My Shop on Sales Ops"
    assert url in msg.text and "7 days" in msg.text and "as manager" in msg.text
    assert msg.html is not None
    assert msg.html.count(url) == 3  # button, spelled-out link, and its text
    assert 'src="https://ops.example.com/logo.png"' in msg.html
    assert "Accept invite" in msg.html and "7 days" in msg.html
    assert "<b>Olive Owner</b>" in msg.html and "<b>My Shop</b>" in msg.html
    assert "mia@shop.test" in msg.html  # the footer says who it was addressed to


def test_invite_escapes_everything_a_person_typed(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://ops.example.com")
    msg = mail_templates.invite(
        to="x@shop.test",
        inviter_name=HOSTILE,
        organization="Ted & Co <script>",
        role="viewer",
        url="https://ops.example.com/invite/t",
        days=1,
    )
    assert "<img src=x" not in msg.html and "<script>" not in msg.html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in msg.html
    assert "Ted &amp; Co &lt;script&gt;" in msg.html
    assert "1 day." in msg.html and "1 day." in msg.text
    assert HOSTILE in msg.text  # plain text is not rendered; it stays as typed


def test_password_reset_bodies(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://ops.example.com/")
    url = "https://ops.example.com/reset/tok"
    msg = mail_templates.password_reset(to="owner@shop.test", url=url, hours=2)
    assert msg.subject == "Reset your Sales Ops password"
    assert url in msg.text and "2 hours" in msg.text and "used once" in msg.text
    assert msg.html.count(url) == 3
    assert "Reset password" in msg.html and "owner@shop.test" in msg.html
    assert 'src="https://ops.example.com/logo.png"' in msg.html  # trailing slash folded
    assert "your password stays the same" in msg.html


def test_html_is_table_based_with_inline_styles_only(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://ops.example.com")
    html = mail_templates.password_reset(to="a@b.test", url="https://o/r/t", hours=2).html
    assert "<style" not in html and "<link" not in html  # Gmail strips both
    assert html.count('role="presentation"') >= 4
    assert re.search(r'<a href="https://o/r/t" style="[^"]*background', html) is None
    assert 'content="light"' in html  # dark-mode clients keep the navy header legible
