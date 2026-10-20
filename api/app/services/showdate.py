"""Show date in the shop's zone; before 5 AM counts as the night before (as in the extension)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SHOP_TZ = ZoneInfo("America/Chicago")
ROLLOVER_HOUR = 5


def show_date(started_at: datetime, tz: ZoneInfo = SHOP_TZ) -> date:
    local = started_at.astimezone(tz)
    d = local.date()
    if local.hour < ROLLOVER_HOUR:
        d -= timedelta(days=1)
    return d
