"""Error text that is safe to log or store: no row values."""

import traceback


def error_line(e: BaseException) -> str:
    """One line about `e`. For a database error, its class and Postgres's primary message:
    the DETAIL ("Failing row contains (… $argon2id$… …)", "Key (email)=(…)") and the
    statement's parameters are left out."""
    from sqlalchemy.exc import DBAPIError

    if isinstance(e, DBAPIError) and e.orig is not None:
        diag = getattr(e.orig, "diag", None)
        primary = getattr(diag, "message_primary", None) or "database error"
        return f"{type(e.orig).__name__}: {primary}"
    return "".join(traceback.format_exception_only(type(e), e)).strip()
