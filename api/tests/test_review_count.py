"""The Review page names how many decisions are waiting, even past what one page lists."""

from app.db import db
from app.models import ReviewItem, ReviewStatus
from app.routes.review import REVIEW_PAGE


def _open(world, n, status=ReviewStatus.open):
    with db.session.begin_nested():
        db.session.add_all(
            ReviewItem(
                organization_id=world["org"],
                show_id=world["show"],
                reason="other",
                summary=f"made-up decision {i}",
                status=status,
            )
            for i in range(n)
        )
    db.session.commit()


def test_the_review_list_says_how_many_are_waiting_when_it_shows_only_the_oldest(
    app, client, world, sign_in
):
    with app.app_context():
        _open(world, REVIEW_PAGE + 7)
        _open(world, 2, status=ReviewStatus.resolved)  # never counted as waiting
    sign_in(world["org"])
    got = client.get("/api/review").get_json()
    assert len(got["items"]) == REVIEW_PAGE
    assert got["total"] == REVIEW_PAGE + 7
    assert got["more"] is True
    # Oldest first: the newest seven are the ones the page leaves out.
    ids = [i["id"] for i in got["items"]]
    assert ids == sorted(ids)


def test_a_short_review_list_has_its_own_count_and_no_more(app, client, world, sign_in):
    with app.app_context():
        _open(world, 3)
    sign_in(world["org"])
    got = client.get("/api/review").get_json()
    assert (len(got["items"]), got["total"], got["more"]) == (3, 3, False)


def test_a_show_past_the_page_is_still_listed_and_can_be_opened(app, client, world, sign_in):
    """The oldest page of decisions all belong to an older show; last night's show must still
    be in the list (with its own count) and open on its own decisions."""
    from datetime import date, timedelta

    from app.models import Show

    with app.app_context():
        old = db.session.get(Show, world["show"])
        new = Show(
            organization_id=world["org"],
            show_key="last-night",
            started_at=old.started_at + timedelta(days=3),
            show_date=old.show_date + timedelta(days=3),
        )
        db.session.add(new)
        db.session.commit()
        new_id = new.id
        _open(world, REVIEW_PAGE + 2)  # all on the older show
        db.session.add_all(
            ReviewItem(
                organization_id=world["org"],
                show_id=new_id,
                reason="price_mismatch",
                summary=f"made-up newer decision {i}",
                status=ReviewStatus.open,
            )
            for i in range(3)
        )
        db.session.commit()
        old_id = world["show"]
        assert isinstance(old.show_date, date)
    sign_in(world["org"])
    got = client.get("/api/review").get_json()
    assert got["more"] is True and got["total"] == REVIEW_PAGE + 5
    by_show = {s["showId"]: s for s in got["shows"]}
    assert by_show[new_id]["count"] == 3 and by_show[new_id]["byReason"] == {"price_mismatch": 3}
    assert by_show[old_id]["count"] == REVIEW_PAGE + 2
    assert [s["showId"] for s in got["shows"]] == [new_id, old_id]  # newest show first
    one = client.get(f"/api/review?showId={new_id}").get_json()
    assert (len(one["items"]), one["total"], one["more"]) == (3, 3, False)
    assert {i["showId"] for i in one["items"]} == {new_id}


def test_a_short_review_list_carries_no_show_summary(app, client, world, sign_in):
    with app.app_context():
        _open(world, 2)
    sign_in(world["org"])
    assert client.get("/api/review").get_json()["shows"] == []
    assert client.get("/api/review?showId=abc").status_code == 400
