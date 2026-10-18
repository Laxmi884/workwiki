# ruff: noqa: T201  (a CLI prints what it did)
"""Schedule the morning rollover (decision 0017): an Agent Server cron, or Mac launchd.

uv run python -m llm_wiki.schedule server install --at 07:00 --url http://127.0.0.1:8123
uv run python -m llm_wiki.schedule server show | remove
uv run python -m llm_wiki.schedule launchd install --at 07:00
uv run python -m llm_wiki.schedule launchd show | remove

Use one, not both (rollover is idempotent, so both would only waste a run). `install`
replaces an earlier rollover schedule of the same kind. Guide: `docs/scheduling.md`.

**server**: a cron on the Agent Server runs the `rollover` graph. Cron times are in a
time zone (`--timezone`, else `WIKI_TIMEZONE`, else this machine's), and the run gets
the same zone, so the note is named by your day, not the container's UTC day.

**launchd**: a LaunchAgent runs `python -m llm_wiki.rollover` at the time on the Mac's
clock, again at each login (`RunAtLoad`; harmless, rollover never overwrites), and once
on wake if the Mac slept through the time. It runs this checkout's Python from this
folder, so `.env` is read as usual; logs go to `~/Library/Logs/llm-wiki/rollover.log`.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from llm_wiki import clock
from llm_wiki.config import get_settings, load_env_file

GRAPH = "rollover"
JOB = {"job": "rollover"}  # metadata on our crons, so install/remove find only ours
LABEL = "com.llm-wiki.rollover"
AGENTS_DIR = Path("~/Library/LaunchAgents")
LOG_PATH = Path("~/Library/Logs/llm-wiki/rollover.log")
DEFAULT_URL = "http://127.0.0.1:2024"

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ScheduleError(Exception):
    """The schedule can't be set: bad time, no time zone, not a Mac, ..."""


def parse_at(text: str) -> tuple[int, int]:
    """Return (hour, minute) from "HH:MM", 24-hour."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if match is None or int(match[1]) > 23 or int(match[2]) > 59:
        raise ScheduleError(f"--at takes a 24-hour time like 07:00, not {text!r}")
    return int(match[1]), int(match[2])


# --- server: an Agent Server cron -----------------------------------------------


def host_zone(localtime: Path = Path("/etc/localtime")) -> str | None:
    """Return this machine's IANA zone from the /etc/localtime link, if it is one."""
    try:
        target = os.readlink(localtime)
    except OSError:
        return None
    _, sep, name = target.partition("zoneinfo/")
    if not sep:
        return None
    try:
        return clock.check_zone(name)
    except ValueError:
        return None


def pick_zone(given: str | None) -> str:
    """Return `--timezone`, else `WIKI_TIMEZONE`, else this machine's zone."""
    try:
        zone = clock.check_zone(given) if given else None
        zone = zone or clock.timezone_name() or host_zone()
    except ValueError as exc:
        raise ScheduleError(str(exc)) from exc
    if zone is None:
        raise ScheduleError(
            "no time zone: pass --timezone or set WIKI_TIMEZONE, e.g. America/Toronto"
        )
    return zone


def server_crons(client: Any) -> list[dict[str, Any]]:
    """Return our rollover crons on the server, found by their `JOB` tag.

    Not by `assistant_id`: search takes only an assistant's UUID, though create
    takes the graph name (langgraph-api 0.16.1).
    """
    return list(client.crons.search(metadata=JOB, limit=100))


def install_server(client: Any, hour: int, minute: int, zone: str) -> dict[str, Any]:
    """Replace our rollover crons with one at hour:minute in `zone`; return it."""
    for cron in server_crons(client):
        client.crons.delete(cron["cron_id"])
    return dict(
        client.crons.create(
            GRAPH,
            schedule=f"{minute} {hour} * * *",
            timezone=zone,
            input={"timezone": zone},
            metadata=JOB,
        )
    )


def remove_server(client: Any) -> int:
    """Delete our rollover crons; return how many there were."""
    crons = server_crons(client)
    for cron in crons:
        client.crons.delete(cron["cron_id"])
    return len(crons)


# --- launchd: a Mac LaunchAgent -------------------------------------------------


def launchd_plist(
    *,
    python: str,
    workdir: Path,
    vault: Path,
    hour: int,
    minute: int,
    log: Path,
    path_env: str,
) -> bytes:
    """Return the LaunchAgent plist that runs rollover daily at hour:minute."""
    job = {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "llm_wiki.rollover", "--vault", str(vault)],
        "WorkingDirectory": str(workdir),
        "EnvironmentVariables": {"PATH": path_env},  # launchd's own PATH is minimal
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    return plistlib.dumps(job, sort_keys=False)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install_launchd(
    plist: bytes,
    agents_dir: Path = AGENTS_DIR,
    log: Path = LOG_PATH,
    run: Runner = subprocess.run,
) -> Path:
    """Write the plist and (re)load it with launchctl; return its path."""
    if sys.platform != "darwin":
        raise ScheduleError("launchd is macOS only; on a server use `schedule server`")
    path = agents_dir.expanduser() / f"{LABEL}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    log.expanduser().parent.mkdir(parents=True, exist_ok=True)
    run(
        ["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True
    )
    path.write_bytes(plist)
    loaded = run(
        ["launchctl", "bootstrap", _domain(), str(path)], capture_output=True, text=True
    )
    if loaded.returncode != 0:
        raise ScheduleError(f"launchctl bootstrap failed: {loaded.stderr.strip()}")
    return path


def remove_launchd(agents_dir: Path = AGENTS_DIR, run: Runner = subprocess.run) -> bool:
    """Unload and delete the LaunchAgent; return whether there was one."""
    path = agents_dir.expanduser() / f"{LABEL}.plist"
    run(
        ["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True
    )
    if not path.exists():
        return False
    path.unlink()
    return True


# --- CLI -------------------------------------------------------------------------


def _client(url: str) -> Any:
    from langgraph_sdk import get_sync_client

    from llm_wiki.backend.server import auth_headers

    return get_sync_client(url=url, headers=auth_headers())


def _server(args: argparse.Namespace) -> None:
    client = _client(args.url)
    if args.action == "install":
        hour, minute = parse_at(args.at)
        zone = pick_zone(args.timezone)
        cron = install_server(client, hour, minute, zone)
        print(f"Rollover cron on {args.url}: daily at {hour:02d}:{minute:02d} {zone}")
        print(f"next run: {cron.get('next_run_date')} (cron {cron['cron_id']})")
    elif args.action == "remove":
        print(f"Removed {remove_server(client)} rollover cron(s) from {args.url}.")
    else:
        crons = server_crons(client)
        for cron in crons:
            print(
                f"{cron['schedule']} · next {cron.get('next_run_date')} "
                f"· {cron['cron_id']}"
            )
        if not crons:
            print(f"No rollover cron on {args.url}.")


def _launchd(args: argparse.Namespace) -> None:
    path = AGENTS_DIR.expanduser() / f"{LABEL}.plist"
    if args.action == "install":
        hour, minute = parse_at(args.at)
        vault = (
            Path(args.vault).expanduser().resolve()
            if args.vault
            else get_settings().vault_path
        )
        plist = launchd_plist(
            python=sys.executable,
            workdir=Path.cwd(),
            vault=vault,
            hour=hour,
            minute=minute,
            log=LOG_PATH.expanduser(),
            path_env=os.environ.get("PATH", "/usr/bin:/bin"),
        )
        install_launchd(plist)
        print(f"Rollover LaunchAgent: daily at {hour:02d}:{minute:02d} (Mac clock)")
        print(f"for {vault}; job {path}; log {LOG_PATH}")
    elif args.action == "remove":
        print("Removed." if remove_launchd() else "No rollover LaunchAgent installed.")
    elif path.exists():
        print(path.read_text())
    else:
        print("No rollover LaunchAgent installed.")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and install, show or remove the schedule."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kinds = parser.add_subparsers(dest="kind", required=True)
    server = kinds.add_parser("server", help="a cron on the Agent Server")
    server.add_argument("action", choices=["install", "show", "remove"])
    server.add_argument("--at", default="07:00", help="HH:MM, 24-hour (default 07:00)")
    server.add_argument(
        "--timezone", help="IANA name (default: WIKI_TIMEZONE, else this machine's)"
    )
    server.add_argument(
        "--url", help=f"the Agent Server (default: WIKI_SERVER_URL, else {DEFAULT_URL})"
    )
    launchd = kinds.add_parser("launchd", help="a LaunchAgent on this Mac")
    launchd.add_argument("action", choices=["install", "show", "remove"])
    launchd.add_argument("--at", default="07:00", help="HH:MM, 24-hour (default 07:00)")
    launchd.add_argument("--vault", help="vault folder (default: WIKI_VAULT_PATH)")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        if args.kind == "server":
            args.url = args.url or os.environ.get("WIKI_SERVER_URL") or DEFAULT_URL
            _server(args)
        else:
            _launchd(args)
    except ScheduleError as exc:
        print(f"schedule: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
