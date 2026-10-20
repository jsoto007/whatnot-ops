from datetime import UTC, date, datetime

from app.services.showdate import show_date


def test_evening_show_is_that_day():
    # 8 PM Chicago on Oct 19 (CDT, UTC-5) = 01:00 UTC Oct 20
    assert show_date(datetime(2026, 10, 20, 1, 0, tzinfo=UTC)) == date(2026, 10, 19)


def test_before_5am_counts_as_the_night_before():
    # 2 AM Chicago on Oct 20 = 07:00 UTC Oct 20
    assert show_date(datetime(2026, 10, 20, 7, 0, tzinfo=UTC)) == date(2026, 10, 19)


def test_5am_is_a_new_day():
    assert show_date(datetime(2026, 10, 20, 10, 0, tzinfo=UTC)) == date(2026, 10, 20)
