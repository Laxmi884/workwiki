"""What Slack shows, kept across restarts: the notifier and the handlers share it."""

import json

from llm_wiki.slack.announced import Announced, Shown

SHOWN = Shown(channel="D1", ts="100.1", seen="abc")


def test_a_record_survives_a_restart(tmp_path):
    path = tmp_path / "state" / "slack-announced.json"
    Announced(path).record("ingest-1", SHOWN)
    again = Announced(path)
    assert again.get("ingest-1") == SHOWN
    again.forget("ingest-1")
    assert Announced(path).items() == []


def test_a_bad_file_counts_as_empty(tmp_path, caplog):
    path = tmp_path / "slack-announced.json"
    for text in ("", "{not json", '{"ingest-1": {"channel": 1}}', "[]"):
        path.write_text(text, encoding="utf-8")
        assert Announced(path).items() == []
    assert "slack-announced.json" in caplog.text


def test_a_write_that_fails_keeps_the_record_in_memory(tmp_path, caplog):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    announced = Announced(blocked / "slack-announced.json")  # its parent is a file
    announced.record("ingest-1", SHOWN)
    assert announced.get("ingest-1") == SHOWN
    assert "could not save" in caplog.text


def test_writes_are_whole_files(tmp_path):
    path = tmp_path / "slack-announced.json"
    announced = Announced(path)
    announced.record("ingest-1", SHOWN)
    announced.record("ingest-2", SHOWN)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"ingest-1", "ingest-2"}
    assert [p.name for p in tmp_path.iterdir()] == ["slack-announced.json"]


def test_idle_only_when_no_run_is_busy():
    announced = Announced(None)
    assert announced.idle()
    with announced.busy():
        assert not announced.idle()
    assert announced.idle()
