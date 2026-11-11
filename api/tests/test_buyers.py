"""Buyer profiles: figures from the shop's sales, names tied to Whatnot's account id, the
team's flag and note, and the laptop's "has this winner bought before?" lookup.

The Whatnot side runs on rows shaped like the mirror of its orders (and once through the
in-memory fake). No test has met a real renamed buyer: that Whatnot's account id stays
the same across a rename is what its API description says, not something seen live.
"""

import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    AuditLog,
    Buyer,
    BuyerName,
    Order,
    OrderStatus,
    Organization,
    Role,
    Sale,
    SaleStatus,
    Show,
    User,
    WhatnotOrder,
    WhatnotSyncState,
)
from app.services import activity, buyers, dashboard
from app.services.devices import create_device
from app.services.whatnot import sync
from tests.test_pipeline import run

T0 = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)


def show_on(app, org_id: int, day: date, key: str | None = None) -> int:
    with app.app_context():
        s = Show(
            organization_id=org_id,
            show_key=key or f"{day.isoformat()}T20:00:00.000Z",
            started_at=datetime(day.year, day.month, day.day, 20, tzinfo=UTC),
            show_date=day,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def sale(app, org_id, show_id, buyer, *, qty=1, price=2500, status="paid", item="Lot #1") -> int:
    with app.app_context():
        s = Sale(
            organization_id=org_id,
            show_id=show_id,
            buyer_username=buyer,
            item_text=item,
            quantity=qty,
            price_cents=price,
            status=SaleStatus(status),
        )
        db.session.add(s)
        db.session.commit()
        return s.id


_seq = iter(range(1, 10_000))


def whatnot_order(app, org_id, uid, username, at=T0) -> None:
    """One order as the mirror of Whatnot's orders holds it."""
    with app.app_context():
        db.session.add(
            WhatnotOrder(
                organization_id=org_id,
                whatnot_id=str(900_000 + next(_seq)),
                status="pending_fulfillment",
                buyer_id=uid,
                buyer_username=username,
                ordered_at=at,
            )
        )
        db.session.commit()


def link(app, org_id, **kw) -> int:
    with app.app_context():
        n = buyers.link(org_id, **kw)
        db.session.commit()
        return n


def person(app, org_id, name) -> dict | None:
    """The profile holding `name`: its row and its names."""
    with app.app_context():
        b = buyers.holders(org_id, [name]).get(name.lower())
        if b is None:
            return None
        names = db.session.scalars(
            select(BuyerName.name_lower).where(BuyerName.buyer_id == b.id).order_by(BuyerName.id)
        ).all()
        return {
            "id": b.id,
            "uid": b.whatnot_user_id,
            "username": b.username,
            "flag": b.flag,
            "note": b.note,
            "names": names,
        }


def owner(app, org_id) -> int:
    with app.app_context():
        u = User(
            organization_id=org_id,
            email=f"owner{org_id}@x.test",
            name="Olive Owner",
            role=Role.owner,
            password_hash="x",
            session_token=uuid.uuid4().hex,
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def note(app, org_id, user_id, name, **changes) -> None:
    with app.app_context():
        buyers.annotate(org_id, name, user=db.session.get(User, user_id), **changes)
        db.session.commit()


def sentences(app, org_id) -> list[str]:
    with app.app_context():
        rows = db.session.scalars(
            select(AuditLog).where(AuditLog.organization_id == org_id).order_by(AuditLog.id)
        ).all()
        return [activity.describe(a.action, a.details) for a in rows if a.action[:6] == "buyer."]


# ---------- figures ----------


def test_a_buyers_figures_are_what_stands_what_was_paid_and_what_fell_through(app, world):
    """Worked example, in cents. Oct 20: 2 × $25.00 paid and 1 × $7.00 shipped. Oct 27:
    1 × $12.00 still unpaid (it stands, so it is an item, but it is not spend), one win
    cancelled and one whose payment failed (neither is an item or spend).
    Items 2 + 1 + 1 = 4; spent 5000 + 700 = 5700; 2 shows."""
    org = world["org"]
    first, second = show_on(app, org, date(2026, 10, 20)), show_on(app, org, date(2026, 10, 27))
    sale(app, org, first, "lani808", qty=2, price=2500, status="paid")
    sale(app, org, first, "Lani808", qty=1, price=700, status="fulfilled")  # same person
    sale(app, org, second, "lani808", qty=1, price=1200, status="reserved")
    sale(app, org, second, "lani808", qty=1, price=900, status="cancelled")
    sale(app, org, second, "lani808", qty=3, price=3000, status="payment_failed")
    sale(app, org, second, "kat.c", qty=1, price=100, status="paid")
    with app.app_context():
        data, lows = buyers.profile(org, "@LANI808")
    assert lows == ["lani808"]
    assert {
        k: data[k]
        for k in ("items", "paidCents", "shows", "firstShow", "lastShow", "cancelled", "failed")
    } == {
        "items": 4,
        "paidCents": 5700,
        "shows": 2,
        "firstShow": "2026-10-20",
        "lastShow": "2026-10-27",
        "cancelled": 1,
        "failed": 1,
    }
    assert data["known"] and not data["followed"] and data["flag"] is None
    assert [(p["status"], p["priceCents"], p["quantity"]) for p in data["problems"]] == [
        ("payment_failed", 3000, 3),
        ("cancelled", 900, 1),
    ]


def test_two_recordings_of_one_evening_count_as_one_show(app, world):
    org = world["org"]
    day = date(2026, 10, 20)
    a, b = show_on(app, org, day, "a"), show_on(app, org, day, "b")
    sale(app, org, a, "lani808")
    sale(app, org, b, "lani808")
    with app.app_context():
        assert buyers.profile(org, "lani808")[0]["shows"] == 1
        # In the evening's second recording they are still a first-time buyer.
        assert buyers.badges(org, ["lani808"], before=day)["lani808"]["firstTime"] is True
        assert buyers.badges(org, ["lani808"], before=day + timedelta(days=1))["lani808"] == {
            "flag": None,
            "firstTime": False,
            "shows": 1,
            "items": 2,
        }


def test_giveaways_count_only_when_the_host_or_the_platform_said_so(app, world):
    world["say"](
        app,
        [
            ("", "lani808 won the giveaway!"),  # the platform's own line
            ("lani808", "won the giveaway!"),  # a viewer typing it
        ],
    )
    run(app, world["show"])
    with app.app_context():
        assert buyers.profile(world["org"], "lani808")[0]["gifts"] == 1
        assert buyers.profile(world["org"], "lani808")[0]["known"] is True
        assert dashboard.buyer_gifts(world["org"], []) == 0


# ---------- who is who ----------


def test_whatnot_naming_an_account_makes_a_profile_and_a_rename_keeps_everything(app, world):
    org = world["org"]
    user = owner(app, org)
    first, second = show_on(app, org, date(2026, 10, 20)), show_on(app, org, date(2026, 11, 3))
    sale(app, org, first, "kat.c", qty=2, price=1000)
    whatnot_order(app, org, "77", "kat.c", T0)
    assert link(app, org) == 1
    assert person(app, org, "kat.c") == {
        "id": 1,
        "uid": "77",
        "username": "kat.c",
        "flag": None,
        "note": None,
        "names": ["kat.c"],
    }
    note(app, org, user, "kat.c", flag="watch", note="Two payments failed in October.")

    # She renames herself and wins again under the new name.
    sale(app, org, second, "kat_new", qty=1, price=500)
    whatnot_order(app, org, "77", "kat_new", T0 + timedelta(days=2))
    assert link(app, org) == 1
    assert person(app, org, "KAT_NEW") == person(app, org, "kat.c")
    p = person(app, org, "kat_new")
    assert (p["username"], p["flag"], p["names"]) == ("kat_new", "watch", ["kat.c", "kat_new"])
    with app.app_context():
        data, lows = buyers.profile(org, "kat.c")  # the old name still finds her
        assert sorted(lows) == ["kat.c", "kat_new"]
        assert (data["username"], data["names"], data["followed"]) == ("kat_new", ["kat.c"], True)
        assert (data["items"], data["paidCents"], data["shows"]) == (3, 2500, 2)
        assert data["note"] == "Two payments failed in October."
        assert data["notedBy"] == "Olive Owner"
        # Any page that shows either name can mark her.
        assert buyers.flags(org) == {
            "kat.c": {"flag": "watch", "note": True},
            "kat_new": {"flag": "watch", "note": True},
        }
        row = buyers.directory(org)["buyers"][0]
        assert (row["username"], row["names"], row["items"]) == ("kat_new", ["kat.c"], 3)
    assert sentences(app, org) == [
        "Put buyer “kat.c” on the watch list",
        "Wrote the note on buyer “kat.c”",
        "Buyer “kat.c” now goes by “kat_new” on Whatnot",
    ]
    assert link(app, org) == 0  # nothing new: nothing done


def test_an_old_order_read_late_never_brings_an_old_name_back(app, world):
    org = world["org"]
    whatnot_order(app, org, "77", "newname", T0 + timedelta(days=5))
    link(app, org)
    whatnot_order(app, org, "77", "oldname", T0)  # an order from before the rename
    assert link(app, org) == 1
    p = person(app, org, "oldname")
    assert (p["username"], sorted(p["names"])) == ("newname", ["newname", "oldname"])
    assert sentences(app, org) == []  # nobody renamed themselves just now
    assert link(app, org, full=True) == 0


def test_only_the_spelling_changing_is_not_a_rename(app, world):
    org = world["org"]
    whatnot_order(app, org, "77", "kat.c", T0)
    link(app, org)
    whatnot_order(app, org, "77", "Kat.C", T0 + timedelta(days=1))
    link(app, org, full=True)
    p = person(app, org, "kat.c")
    assert (p["username"], p["names"]) == ("Kat.C", ["kat.c"])
    assert sentences(app, org) == []


def test_a_profile_the_team_made_for_a_name_is_tied_to_the_account_that_uses_it(app, world):
    org = world["org"]
    user = owner(app, org)
    note(app, org, user, "@bob", flag="vip")  # flagged before Whatnot told us who bob is
    before = person(app, org, "bob")
    assert (before["uid"], before["flag"]) == (None, "vip")
    whatnot_order(app, org, "9", "bob")
    assert link(app, org) == 1
    after = person(app, org, "bob")
    assert (after["id"], after["uid"], after["flag"]) == (before["id"], "9", "vip")
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Buyer)) == 1


def test_two_profiles_of_one_account_are_joined_and_the_cautious_flag_stands(app, world):
    org = world["org"]
    user = owner(app, org)
    whatnot_order(app, org, "5", "amy", T0)
    link(app, org)
    note(app, org, user, "amy", flag="vip", note="Buys every week.")
    note(app, org, user, "amy2", flag="watch", note="Asked for a refund twice.")
    whatnot_order(app, org, "5", "amy2", T0 + timedelta(days=1))  # amy2 is amy's new name
    assert link(app, org) == 1
    p = person(app, org, "amy")
    assert p == person(app, org, "amy2")
    assert (p["uid"], p["username"], p["flag"], sorted(p["names"])) == (
        "5",
        "amy2",
        "watch",
        ["amy", "amy2"],
    )
    assert p["note"] == "Buys every week.\nAsked for a refund twice."
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Buyer)) == 1
    assert sentences(app, org)[-2:] == [
        "Buyer “amy2” and buyer “amy” are one Whatnot account: their profiles were joined",
        "Buyer “amy” now goes by “amy2” on Whatnot",
    ]


def test_a_name_another_account_takes_over_moves_only_on_a_newer_order(app, world):
    """A released name is someone else's now: the watch flag must not land on them, and
    must find the first account again when it orders under a new name."""
    org = world["org"]
    user = owner(app, org)
    whatnot_order(app, org, "1", "sam", T0)
    link(app, org)
    note(app, org, user, "sam", flag="watch")
    whatnot_order(app, org, "2", "sam", T0 + timedelta(days=30))  # a different account
    assert link(app, org) == 1
    p = person(app, org, "sam")
    assert (p["uid"], p["flag"]) == ("2", None)
    with app.app_context():
        assert buyers.flags(org) == {}
        assert buyers.directory(org, flag="watch")["buyers"] == []
    # Reading everything again (account 1's old order still says "sam") changes nothing,
    # and finds nothing to do.
    assert link(app, org, full=True) == 0
    assert person(app, org, "sam")["uid"] == "2"
    assert sentences(app, org)[-1] == "The name “sam” is now used by a different Whatnot account"
    # Account 1 comes back under another name: the flag is on it at once.
    whatnot_order(app, org, "1", "sam_again", T0 + timedelta(days=40))
    link(app, org)
    p = person(app, org, "sam_again")
    assert (p["uid"], p["flag"], p["username"]) == ("1", "watch", "sam_again")


def test_linking_reads_only_new_orders_and_everything_once_a_day(app, world, monkeypatch):
    monkeypatch.setattr(buyers, "CURSOR_OVERLAP", 0)  # the overlap has its own test below
    org = world["org"]
    whatnot_order(app, org, "1", "a")
    whatnot_order(app, org, "1", "a")
    whatnot_order(app, org, "2", "b")
    assert link(app, org) == 2  # two (account, name) pairs, however many orders
    with app.app_context():
        state = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == buyers.STATE)
        )
        top = state.cursor["order_id"]
        assert top == db.session.scalar(select(func.max(WhatnotOrder.id)))
        # An order below the cursor that names its buyer only now (the token gained the
        # permission): missed by the next quick pass, found by the daily one.
        o = db.session.scalar(select(WhatnotOrder).where(WhatnotOrder.buyer_username == "b"))
        o.buyer_id, o.buyer_username = "3", "c"
        db.session.commit()
    assert link(app, org) == 0
    assert person(app, org, "c") is None
    with app.app_context():
        state = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == buyers.STATE)
        )
        state.cursor = {
            **state.cursor,
            "full_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
        db.session.commit()
    assert link(app, org) == 1
    assert person(app, org, "c")["uid"] == "3"


def test_orders_read_from_whatnot_bring_their_buyers_along(app, world, fake_whatnot, monkeypatch):
    """The real path: an order stored by the sync, then the job the sync queues."""
    monkeypatch.setenv("JOBS_INLINE", "1")
    org = world["org"]
    data = fake_whatnot.add_order(
        show_id=None, buyer="lani808", buyer_id="4242", items=[{"title": "Lot #1"}]
    )
    with app.app_context():
        sync.upsert_order(org, data)
        db.session.commit()
        sync._after_orders(org, {None})
        db.session.commit()
    p = person(app, org, "lani808")
    assert (p["uid"], p["username"]) == ("4242", "lani808")


def test_two_people_flagging_a_new_buyer_at_once_make_one_profile(app, world):
    org = world["org"]
    user = owner(app, org)
    whatnot_order(app, org, "8", "zed")
    errors: list[Exception] = []
    gate = threading.Barrier(13)

    def flagger(i: int):
        with app.app_context():
            try:
                gate.wait(timeout=10)
                buyers.annotate(
                    org, "ZED" if i % 2 else "zed", user=db.session.get(User, user), flag="vip"
                )
                db.session.commit()
            except Exception as e:  # noqa: BLE001 - any failure fails the test below
                db.session.rollback()
                errors.append(e)
            finally:
                db.session.remove()

    def linker():
        with app.app_context():
            try:
                gate.wait(timeout=10)
                buyers.link(org)
                db.session.commit()
            except Exception as e:  # noqa: BLE001
                db.session.rollback()
                errors.append(e)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=flagger, args=(i,)) for i in range(12)]
    threads.append(threading.Thread(target=linker))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Buyer)) == 1
        assert db.session.scalar(select(func.count()).select_from(BuyerName)) == 1
    p = person(app, org, "zed")
    assert (p["uid"], p["flag"]) == ("8", "vip")
    assert sentences(app, org) == ["Marked buyer “zed” as a VIP"]  # said once, not twelve times


# ---------- found by the review of this phase (each failed before its fix) ----------


def profiles(app) -> int:
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(Buyer))


def test_two_accounts_with_one_name_at_the_same_instant_do_not_take_turns(app, world):
    """Equal timestamps used to let each account reclaim the name on every daily pass,
    carrying the watch flag from one person to the other and back."""
    org = world["org"]
    user = owner(app, org)
    whatnot_order(app, org, "1", "bob", T0)
    whatnot_order(app, org, "2", "bob", T0)
    link(app, org)
    first = person(app, org, "bob")["uid"]
    note(app, org, user, "bob", flag="watch")
    for _ in range(3):
        assert link(app, org, full=True) == 0
        p = person(app, org, "bob")
        assert (p["uid"], p["flag"]) == (first, "watch")
    assert sentences(app, org) == ["Put buyer “bob” on the watch list"]
    assert profiles(app) == 1  # the account that does not hold the name has no profile


def test_orders_without_a_date_never_rename_a_buyer_or_take_a_name(app, world):
    """Two undated orders with two names used to swap the buyer's name every day."""
    org = world["org"]
    whatnot_order(app, org, "1", "a", None)
    whatnot_order(app, org, "1", "b", None)
    link(app, org)
    p = person(app, org, "a")
    assert sorted(p["names"]) == ["a", "b"]  # both names are theirs
    called = p["username"]
    for _ in range(3):
        assert link(app, org, full=True) == 0
        assert person(app, org, "a")["username"] == called
    assert sentences(app, org) == []
    # An undated order of another account cannot take a name away either.
    whatnot_order(app, org, "2", "a", None)
    assert link(app, org, full=True) == 0
    assert person(app, org, "a")["uid"] == "1"
    assert profiles(app) == 1
    # A dated order does say what they are called now.
    whatnot_order(app, org, "1", "b", T0)
    assert link(app, org) == 1
    assert person(app, org, "a")["username"] == "b"


def test_a_name_taken_over_does_not_bring_the_earlier_accounts_history(app, client, world, sign_in):
    """Sales are filed by name. The new holder of a re-used name used to be shown the
    earlier account's wins: "Back again" at their first win, and $99.00 they never spent."""
    org = world["org"]
    then, now = show_on(app, org, date(2026, 10, 1)), show_on(app, org, date(2026, 11, 5))
    with app.app_context():
        for show_id in (then, now):
            db.session.add(
                Order(
                    organization_id=org,
                    show_id=show_id,
                    buyer_username="sam",
                    status=OrderStatus.paid,
                )
            )
        db.session.commit()
    sale(app, org, then, "sam", qty=1, price=9900)  # the earlier account's
    sale(app, org, then, "sam", qty=1, price=500, status="cancelled")
    whatnot_order(app, org, "1", "sam", datetime(2026, 10, 2, 1, 0, tzinfo=UTC))
    link(app, org)
    with app.app_context():
        assert buyers.profile(org, "sam")[0]["paidCents"] == 9900  # theirs, so far
    # A different account now uses the name, at the show of Nov 5 (8 PM in the shop).
    whatnot_order(app, org, "2", "sam", datetime(2026, 11, 6, 2, 0, tzinfo=UTC))
    link(app, org)
    with app.app_context():
        assert buyers.badges(org, ["sam"], before=date(2026, 11, 5))["sam"] == {
            "flag": None,
            "firstTime": True,
            "shows": 0,
            "items": 0,
        }
        data, _ = buyers.profile(org, "sam")
        assert (data["items"], data["paidCents"], data["shows"], data["cancelled"]) == (0, 0, 0, 0)
        assert data["problems"] == []
    sale(app, org, now, "sam", qty=2, price=1000)  # the new account's own win
    with app.app_context():
        data, _ = buyers.profile(org, "sam")
        assert (data["items"], data["paidCents"], data["firstShow"]) == (2, 2000, "2026-11-05")
        row = buyers.directory(org)["buyers"]
        assert [(r["username"], r["items"], r["paidCents"]) for r in row] == [("sam", 2, 2000)]
    sign_in(org)
    page = client.get("/api/buyers/by-name/sam").get_json()
    assert [o["showDate"] for o in page["orders"]] == ["2026-11-05"]
    history = client.get("/api/orders/buyers/sam").get_json()
    assert [o["showDate"] for o in history["orders"]] == ["2026-11-05"]


def test_a_sighting_that_loses_makes_no_profile_and_is_not_looked_at_again(app, world):
    """An old order of a name's earlier owner, read late, used to leave an empty profile
    and be acted on again every day."""
    org = world["org"]
    whatnot_order(app, org, "1", "sam", T0 + timedelta(days=30))
    link(app, org)
    whatnot_order(app, org, "2", "sam", T0)
    assert link(app, org) == 0
    assert profiles(app) == 1
    assert person(app, org, "sam")["uid"] == "1"
    assert link(app, org, full=True) == 0


def test_a_team_made_profile_goes_to_the_account_that_uses_the_name_now(app, world):
    """It used to be tied to whichever account's order was read first, even an earlier
    owner of the name: the flag then sat on the wrong person."""
    org = world["org"]
    user = owner(app, org)
    # Flagged when Whatnot already knows the name: tied at once, to its present user.
    whatnot_order(app, org, "1", "bob", T0)
    whatnot_order(app, org, "2", "bob", T0 + timedelta(days=30))
    note(app, org, user, "bob", flag="watch")
    p = person(app, org, "bob")
    assert (p["uid"], p["flag"]) == ("2", "watch")
    assert link(app, org, full=True) == 0
    # Flagged first; the orders of two accounts arrive later, the earlier owner's first.
    note(app, org, user, "amy", flag="vip")
    whatnot_order(app, org, "3", "amy", T0)
    whatnot_order(app, org, "4", "amy", T0 + timedelta(days=30))
    link(app, org)
    p = person(app, org, "amy")
    assert (p["uid"], p["flag"]) == ("4", "vip")
    assert profiles(app) == 2  # bob's and amy's: the earlier owners hold nothing
    assert link(app, org, full=True) == 0


def test_an_order_stored_late_below_the_cursor_is_still_found(app, world):
    """Two workers store orders at once: the lower id can commit after the higher one was
    read. The quick pass looks again a little below where it stopped."""
    org = world["org"]
    whatnot_order(app, org, "1", "a")
    link(app, org)
    with app.app_context():
        state = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == buyers.STATE)
        )
        # As if a later order (a higher id) had already been read and linked.
        state.cursor = {**state.cursor, "order_id": state.cursor["order_id"] + 5}
        db.session.commit()
    whatnot_order(app, org, "2", "b")  # its id is below the cursor
    assert link(app, org) == 1
    assert person(app, org, "b")["uid"] == "2"


def test_a_name_that_grows_when_lower_cased_still_fits(app, client, world, sign_in):
    """60 × "İ" is 120 characters in lower case: saving a flag for it answered 500."""
    from urllib.parse import quote

    h = sign_in(world["org"])
    r = client.patch(f"/api/buyers/by-name/{quote('İ' * 60)}", json={"flag": "vip"}, headers=h)
    assert r.status_code == 200
    kept = r.get_json()["buyer"]["username"]
    assert kept == "İ" * 40 and len(kept.lower()) == 80


# ---------- the pages ----------


@pytest.fixture
def busy(app, world):
    """Three buyers over two show dates; ids of the shop and its shows."""
    org = world["org"]
    first, second = show_on(app, org, date(2026, 10, 20)), show_on(app, org, date(2026, 10, 27))
    sale(app, org, first, "lani808", qty=2, price=2500)
    sale(app, org, second, "lani808", qty=1, price=700)
    sale(app, org, second, "kat.c", qty=5, price=100)
    sale(app, org, second, "kat.c", qty=1, price=900, status="payment_failed")
    sale(app, org, first, "zoe", qty=1, price=9900, status="cancelled")
    return {"org": org, "first": first, "second": second}


def test_the_list_is_one_row_per_person_with_search_filters_and_order(app, client, busy, sign_in):
    h = sign_in(busy["org"])
    rows = client.get("/api/buyers").get_json()
    assert [(r["username"], r["items"], r["paidCents"], r["shows"]) for r in rows["buyers"]] == [
        ("lani808", 3, 5700, 2),
        ("kat.c", 5, 500, 1),
        ("zoe", 0, 0, 0),
    ]
    assert rows["total"] == 3 and rows["counts"] == {"all": 3, "vip": 0, "watch": 0, "noted": 0}

    def names(query: str) -> list[str]:
        return [r["username"] for r in client.get("/api/buyers" + query).get_json()["buyers"]]

    assert names("?sort=items") == ["kat.c", "lani808", "zoe"]
    assert names("?sort=problems") == ["kat.c", "zoe", "lani808"]
    assert names("?sort=name") == ["kat.c", "lani808", "zoe"]
    assert names("?q=LANI") == ["lani808"]
    assert names("?q=@kat") == ["kat.c"]
    assert client.get("/api/buyers?sort=secret").status_code == 400
    assert client.get("/api/buyers?flag=secret").status_code == 400

    # Someone flagged before their first win is listed too, under the filter and in all.
    r = client.patch("/api/buyers/by-name/newcomer", json={"flag": "vip"}, headers=h)
    assert r.status_code == 200 and r.get_json()["buyer"]["flag"] == "vip"
    client.patch("/api/buyers/by-name/kat.c", json={"note": "Loves the red ones."}, headers=h)
    assert names("?flag=vip") == ["newcomer"]
    assert names("?flag=noted") == ["kat.c"]
    listing = client.get("/api/buyers").get_json()
    assert listing["counts"] == {"all": 4, "vip": 1, "watch": 0, "noted": 1}
    assert client.get("/api/buyers/flags").get_json()["flags"] == {
        "newcomer": {"flag": "vip", "note": False},
        "kat.c": {"flag": None, "note": True},
    }


def test_a_buyers_page_has_their_orders_under_every_name(app, client, busy, sign_in):
    org = busy["org"]
    sign_in(org)
    with app.app_context():
        for show_id, name in ((busy["first"], "lani808"), (busy["second"], "lani_new")):
            o = Order(
                organization_id=org, show_id=show_id, buyer_username=name, status=OrderStatus.paid
            )
            db.session.add(o)
            db.session.flush()
            db.session.add(
                Sale(
                    organization_id=org,
                    show_id=show_id,
                    buyer_username=name,
                    item_text="Lot #9",
                    quantity=1,
                    price_cents=400,
                    status=SaleStatus.paid,
                    order_id=o.id,
                )
            )
        db.session.commit()
    whatnot_order(app, org, "77", "lani808", T0)
    whatnot_order(app, org, "77", "lani_new", T0 + timedelta(days=9))
    link(app, org)
    got = client.get("/api/buyers/by-name/lani808").get_json()
    assert got["buyer"]["username"] == "lani_new" and got["buyer"]["names"] == ["lani808"]
    assert [(o["showDate"], o["buyer"], o["totalCents"]) for o in got["orders"]] == [
        ("2026-10-27", "lani_new", 400),
        ("2026-10-20", "lani808", 400),
    ]
    assert got["orders"][0]["sales"] is None  # the page lists orders; it does not need lines
    # The order page's "in other shows" follows the rename too.
    old = client.get("/api/orders/buyers/lani808").get_json()
    assert [o["buyer"] for o in old["orders"]] == ["lani_new", "lani808"]
    # A name nobody has used is answered, not refused: the team may flag it first.
    ghost = client.get("/api/buyers/by-name/nobody").get_json()["buyer"]
    assert (ghost["known"], ghost["items"], ghost["username"]) == (False, 0, "nobody")


def test_who_may_read_and_who_may_flag(app, client, busy, sign_in):
    org = busy["org"]
    h = sign_in(org, "viewer", "v@x.test")
    assert client.get("/api/buyers").status_code == 200
    assert client.get("/api/buyers/by-name/lani808").status_code == 200
    r = client.patch("/api/buyers/by-name/lani808", json={"flag": "vip"}, headers=h)
    assert r.status_code == 403
    client.post("/api/auth/logout", headers=h)
    h = sign_in(org, "fulfillment", "f@x.test")  # the packing table may flag and note
    ok = client.patch(
        "/api/buyers/by-name/lani808",
        json={"flag": "watch", "note": "  Check the address\x07 first.‮  "},
        headers=h,
    )
    assert ok.status_code == 200
    assert ok.get_json()["buyer"]["note"] == "Check the address first."  # no bell, no bidi mark
    assert client.patch("/api/buyers/by-name/lani808", json={}, headers=h).status_code == 400
    bad = client.patch("/api/buyers/by-name/lani808", json={"flag": "enemy"}, headers=h)
    assert bad.status_code == 400 and "VIP" in bad.get_json()["error"]
    for odd in ([], 0, False, {"a": 1}):  # not "no flag": refused, the flag stays
        r = client.patch("/api/buyers/by-name/lani808", json={"flag": odd}, headers=h)
        assert r.status_code == 400
    assert client.get("/api/buyers/by-name/lani808").get_json()["buyer"]["flag"] == "watch"
    # Clearing what was never set makes no profile and no line in the activity log.
    none = client.patch("/api/buyers/by-name/never_seen", json={"flag": None}, headers=h)
    assert none.status_code == 200 and none.get_json()["buyer"]["known"] is False
    with app.app_context():
        assert buyers.holders(org, ["never_seen"]) == {}
    long = client.patch("/api/buyers/by-name/lani808", json={"note": "x" * 2001}, headers=h)
    assert long.status_code == 400
    assert (
        client.patch("/api/buyers/by-name/lani808", json={"note": 5}, headers=h).status_code == 400
    )
    without = client.patch("/api/buyers/by-name/lani808", json={"flag": "vip"})
    assert without.status_code == 403  # no CSRF token
    cleared = client.patch(
        "/api/buyers/by-name/lani808", json={"flag": None, "note": ""}, headers=h
    ).get_json()["buyer"]
    assert (cleared["flag"], cleared["note"]) == (None, None)
    assert sentences(app, org) == [
        "Put buyer “lani808” on the watch list",
        "Wrote the note on buyer “lani808”",
        "Buyer “lani808” is no longer on the watch list",
        "Removed the note on buyer “lani808”",
    ]


def test_another_shop_sees_none_of_it(app, client, busy, sign_in):
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    user = owner(app, busy["org"])
    note(app, busy["org"], user, "lani808", flag="watch", note="private")
    sign_in(other_id)
    assert client.get("/api/buyers").get_json()["buyers"] == []
    assert client.get("/api/buyers/flags").get_json()["flags"] == {}
    theirs = client.get("/api/buyers/by-name/lani808").get_json()
    assert theirs["buyer"]["known"] is False and theirs["buyer"]["note"] is None
    assert theirs["orders"] == []


# ---------- the laptop ----------


def test_the_laptop_learns_who_just_won(app, client, busy):
    org = busy["org"]
    user = owner(app, org)
    note(app, org, user, "kat.c", flag="watch", note="never shown on the laptop")
    with app.app_context():
        _, key = create_device(db.session.get(Organization, org), "show laptop")
        tonight = db.session.get(Show, busy["second"]).show_key
        db.session.commit()
    auth = {"Authorization": "Bearer " + key}
    r = client.post(
        "/api/ingest/buyers",
        json={"show": tonight, "names": ["Lani808", "kat.c", "brand_new"]},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.get_json() == {
        "buyers": [
            # Before tonight: tonight's own wins are not "before".
            {"name": "Lani808", "flag": None, "firstTime": False, "shows": 1, "items": 2},
            {"name": "kat.c", "flag": "watch", "firstTime": True, "shows": 0, "items": 0},
            {"name": "brand_new", "flag": None, "firstTime": True, "shows": 0, "items": 0},
        ]
    }
    assert "never shown" not in r.get_data(as_text=True)
    # A show whose first lines are still on their way up: today's date stands in.
    r = client.post(
        "/api/ingest/buyers", json={"show": "not-yet", "names": ["lani808"]}, headers=auth
    )
    assert r.get_json()["buyers"][0] == {
        "name": "lani808",
        "flag": None,
        "firstTime": False,
        "shows": 2,
        "items": 3,
    }
    for body in ({"names": "lani808"}, {"names": [1]}, {"names": ["a"] * 51}, ["lani808"]):
        assert client.post("/api/ingest/buyers", json=body, headers=auth).status_code == 400
    assert client.post("/api/ingest/buyers", json={"names": ["lani808"]}).status_code == 401
