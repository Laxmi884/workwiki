"""The user's "today" (decision 0017): `WIKI_TIMEZONE`, else the machine's local time.

Daily notes are named by the user's day, so "today" must be theirs, not the machine's:
an Agent Server in Docker runs in UTC, and at 21:00 in Toronto UTC is already tomorrow.
`WIKI_TIMEZONE` is an IANA name ("America/Toronto"). It is read from the process
environment, like the provider keys: our CLIs load `.env` first (`load_env_file`),
`langgraph dev` and `compose.yml` load it themselves.

The `tzdata` package is a dependency, so the names resolve in Python even where the
system has no time-zone files (e.g. Windows). The server image also needs the system
files (`apk add tzdata` in `langgraph.json`): the Agent Server checks a cron's time zone
outside Python and refused every name without them.
"""

from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV = "WIKI_TIMEZONE"


def timezone_name() -> str | None:
    """Return `WIKI_TIMEZONE`, or None when unset or blank.

    Raises:
        ValueError: It isn't a time zone name, e.g. "EST5" or "Toronto".
    """
    name = os.environ.get(ENV, "").strip()
    if not name:
        return None
    check_zone(name)
    return name


def check_zone(name: str) -> str:
    """Return `name` if it is an IANA time zone name, else raise ValueError."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"{name!r} is not an IANA time zone name, e.g. 'America/Toronto'"
        ) from exc
    return name


def now(zone: str | None = None) -> datetime.datetime:
    """Return the aware current time in `zone`, else `WIKI_TIMEZONE`, else local."""
    name = zone or timezone_name()
    if name is None:
        return datetime.datetime.now().astimezone()
    return datetime.datetime.now(ZoneInfo(check_zone(name)))


def today(zone: str | None = None) -> datetime.date:
    """Return the current date in `zone`, else `WIKI_TIMEZONE`, else local."""
    return now(zone).date()
