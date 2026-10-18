"""Scheduling rollover (decision 0017): the user's day, the graph, server cron, launchd."""

import datetime
import plistlib
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from llm_wiki import clock, schedule
from llm_wiki.middleware.current_date import CurrentDateMiddleware
from llm_wiki.rollover_graph import graph
from llm_wiki.schedule import (
    JOB,
    LABEL,
    ScheduleError,
    install_launchd,
    install_server,
    launchd_plist,
    parse_at,
    pick_zone,
    remove_launchd,
    remove_server,
)

# --- the user's day -------------------------------------------------------------


def test_today_is_the_users_day_not_the_machines(monkeypatch):
    # UTC+14 and UTC-11 are 25 hours apart: their dates always differ
    assert clock.today("Pacific/Kiritimati") > clock.today("Pacific/Pago_Pago")
    monkeypatch.setenv("WIKI_TIMEZONE", "Pacific/Kiritimati")
    assert clock.today() == clock.today("Pacific/Kiritimati")
    assert clock.now().utcoffset() == datetime.timedelta(hours=14)


def test_unset_means_local_time_and_a_bad_name_fails_loudly(monkeypatch):
    monkeypatch.delenv("WIKI_TIMEZONE", raising=False)
    assert clock.timezone_name() is None
    assert clock.today() == datetime.date.today()
    monkeypatch.setenv("WIKI_TIMEZONE", "Toronto")
    with pytest.raises(ValueError, match="not an IANA time zone name"):
        clock.today()


def test_the_assistants_date_follows_wiki_timezone():
    assert CurrentDateMiddleware()._today is clock.today


# --- the graph -------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path, fixture_vault):
    root = tmp_path / "vault"
    (root / "50_templates").mkdir(parents=True)
    shutil.copy(fixture_vault / "50_templates/daily.md", root / "50_templates")
    return root


def test_graph_creates_the_note_for_the_given_date(vault):
    out = graph.invoke(
        {"date": "2026-10-19", "commit": False}, context={"vault_path": str(vault)}
    )
    assert out["result"]["path"] == "00_raw/daily/2026-10-19.md"
    assert out["result"]["created"] is True
    assert (vault / out["result"]["path"]).exists()


def test_graph_without_a_date_uses_today_in_the_given_zone(vault, monkeypatch):
    zones = []

    def fake_today(zone=None):
        zones.append(zone)
        return datetime.date(2026, 10, 20)

    monkeypatch.setattr(clock, "today", fake_today)
    out = graph.invoke(
        {"timezone": "America/Toronto", "commit": False},
        context={"vault_path": str(vault)},
    )
    assert zones == ["America/Toronto"]
    assert out["result"]["path"] == "00_raw/daily/2026-10-20.md"


# --- server cron -----------------------------------------------------------------


class FakeCrons:
    def __init__(self, existing=()):
        self.crons = {c["cron_id"]: c for c in existing}
        self.searches = []

    def search(self, **kw):
        self.searches.append(kw)
        if (
            "assistant_id" in kw
        ):  # as the server: "Invalid assistant ID: must be a UUID"
            uuid.UUID(kw["assistant_id"])
        return [c for c in self.crons.values() if c.get("metadata") == kw["metadata"]]

    def delete(self, cron_id):
        del self.crons[cron_id]

    def create(self, assistant_id, **kw):
        cron = {"cron_id": f"c{len(self.crons)}", "assistant_id": assistant_id, **kw}
        self.crons[cron["cron_id"]] = cron
        return cron


class FakeClient:
    def __init__(self, existing=()):
        self.crons = FakeCrons(existing)


def test_server_install_replaces_only_our_cron_and_passes_the_zone():
    theirs = {"cron_id": "x", "metadata": {"job": "brief"}}
    ours = {"cron_id": "old", "metadata": dict(JOB)}
    client = FakeClient([theirs, ours])
    cron = install_server(client, 7, 5, "America/Toronto")
    assert set(client.crons.crons) == {"x", cron["cron_id"]}
    assert cron == {
        "cron_id": cron["cron_id"],
        "assistant_id": "rollover",
        "schedule": "5 7 * * *",
        "timezone": "America/Toronto",
        "input": {"timezone": "America/Toronto"},  # the run's day, not the container's
        "metadata": JOB,
    }
    assert remove_server(client) == 1 and set(client.crons.crons) == {"x"}


def test_zone_from_flag_then_setting_then_this_machine(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TIMEZONE", "Europe/Paris")
    assert pick_zone("Asia/Kolkata") == "Asia/Kolkata"
    assert pick_zone(None) == "Europe/Paris"
    monkeypatch.delenv("WIKI_TIMEZONE")
    link = tmp_path / "localtime"
    link.symlink_to("/var/db/timezone/zoneinfo/America/Toronto")
    assert schedule.host_zone(link) == "America/Toronto"
    monkeypatch.setattr(schedule, "host_zone", lambda: None)
    with pytest.raises(ScheduleError, match="no time zone"):
        pick_zone(None)
    with pytest.raises(ScheduleError, match="not an IANA"):
        pick_zone("EST-ish")


@pytest.mark.parametrize("text", ["7", "24:00", "07:60", "7am", ""])
def test_at_takes_a_24_hour_time(text):
    assert parse_at("7:05") == (7, 5)
    with pytest.raises(ScheduleError):
        parse_at(text)


# --- launchd ---------------------------------------------------------------------


def _plist(tmp_path):
    return launchd_plist(
        python="/repo/.venv/bin/python",
        workdir=Path("/repo"),
        vault=Path("/Users/me/Work"),
        hour=7,
        minute=0,
        log=tmp_path / "rollover.log",
        path_env="/usr/local/bin:/usr/bin:/bin",
    )


def test_plist_runs_rollover_daily_at_load_and_logs(tmp_path):
    job = plistlib.loads(_plist(tmp_path))
    assert job["Label"] == LABEL
    assert job["ProgramArguments"] == [
        "/repo/.venv/bin/python",
        "-m",
        "llm_wiki.rollover",
        "--vault",
        "/Users/me/Work",
    ]
    assert job["WorkingDirectory"] == "/repo"  # where .env is
    assert job["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}
    assert job["RunAtLoad"] is True  # a Mac that was off at 07:00 rolls over at login
    log = str(tmp_path / "rollover.log")
    assert job["StandardOutPath"] == job["StandardErrorPath"] == log
    assert job["EnvironmentVariables"] == {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def test_launchd_install_reloads_and_remove_unloads(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    calls = []

    def run(args, **kw):
        calls.append(args[:2])
        return subprocess.CompletedProcess(args, 0, "", "")

    agents = tmp_path / "LaunchAgents"
    path = install_launchd(_plist(tmp_path), agents, tmp_path / "logs/r.log", run)
    assert path == agents / f"{LABEL}.plist" and path.read_bytes() == _plist(tmp_path)
    assert calls == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    assert remove_launchd(agents, run) is True and not path.exists()
    assert remove_launchd(agents, run) is False


def test_launchd_load_failure_and_other_systems_say_why(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule.sys, "platform", "darwin")

    def failing(args, **kw):
        return subprocess.CompletedProcess(args, 5, "", "Input/output error")

    with pytest.raises(ScheduleError, match="Input/output error"):
        install_launchd(_plist(tmp_path), tmp_path, tmp_path / "r.log", failing)
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    with pytest.raises(ScheduleError, match="macOS only"):
        install_launchd(_plist(tmp_path), tmp_path, tmp_path / "r.log", failing)


# --- the server image ------------------------------------------------------------


def test_the_server_serves_rollover_and_its_image_has_time_zones():
    import json

    config = json.loads((Path(__file__).parents[2] / "langgraph.json").read_text())
    assert config["graphs"]["rollover"] == "./src/llm_wiki/rollover_graph.py:graph"
    # without the system files the server refuses every cron time zone (2026-10-18)
    assert "RUN apk add --no-cache tzdata" in config["dockerfile_lines"]
