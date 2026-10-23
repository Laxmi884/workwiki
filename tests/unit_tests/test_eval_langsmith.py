"""The Q&A dataset mirrored to LangSmith (0019): planned by code, synced without the network."""

from types import SimpleNamespace

from tests.eval import langsmith_qa
from tests.eval.corpus import load_questions


def ex(q, answer="A", keys=(("a",),), id_=None):
    out = {"inputs": {"question": q}, "outputs": {"answer": answer, "keys": keys}}
    return out if id_ is None else {"id": id_, **out}


def test_examples_come_from_the_repo_yaml():
    rows = langsmith_qa.examples()
    questions = load_questions()
    assert len(rows) == len(questions) == 30
    assert len({r["inputs"]["question"] for r in rows}) == 30  # the sync key is unique
    assert rows[0] == {
        "inputs": {"question": questions[0]["q"]},
        "outputs": {"answer": questions[0]["answer"], "keys": questions[0]["keys"]},
    }
    assert sum(r["outputs"]["keys"] == "unanswerable" for r in rows) == 2


def test_plan_creates_updates_and_deletes_by_question():
    wanted = [ex("same"), ex("changed", answer="new"), ex("missing")]
    existing = [
        ex("same", id_="1"),
        ex("changed", answer="old", id_="2"),
        ex("gone", id_="3"),
        ex("same", id_="4"),  # a duplicate is removed
    ]
    plan = langsmith_qa.plan(wanted, existing)
    assert plan.create == [ex("missing")]
    assert plan.update == [ex("changed", answer="new", id_="2")]
    assert plan.delete == ["4", "3"]
    assert not plan.empty


def test_plan_is_empty_when_the_dataset_matches():
    wanted = [ex("a"), ex("b")]
    existing = [ex("b", id_="2"), ex("a", id_="1")]  # order doesn't matter
    assert langsmith_qa.plan(wanted, existing).empty


class FakeClient:
    """The few `langsmith.Client` calls `sync` makes, recorded."""

    def __init__(self, existing=None):
        self.existing = existing  # None: no dataset yet
        self.calls = []

    def has_dataset(self, *, dataset_name):
        return self.existing is not None

    def read_dataset(self, *, dataset_name):
        return SimpleNamespace(id="ds")

    def create_dataset(self, name, *, description):
        self.calls.append(("create_dataset", name))
        return SimpleNamespace(id="ds")

    def list_examples(self, *, dataset_id):
        return [
            SimpleNamespace(id=e["id"], inputs=e["inputs"], outputs=e["outputs"])
            for e in self.existing or []
        ]

    def create_examples(self, *, dataset_id, examples):
        self.calls.append(("create_examples", len(examples)))

    def update_examples(self, *, dataset_id, updates):
        self.calls.append(("update_examples", [u["id"] for u in updates]))

    def delete_examples(self, example_ids):
        self.calls.append(("delete_examples", list(example_ids)))


def test_sync_creates_the_dataset_then_does_nothing_on_a_rerun():
    client = FakeClient()
    plan = langsmith_qa.sync(client)
    assert client.calls == [("create_dataset", "llm-wiki-qa"), ("create_examples", 30)]
    assert len(plan.create) == 30

    uploaded = [{"id": str(n), **e} for n, e in enumerate(langsmith_qa.examples())]
    again = FakeClient(existing=uploaded)
    assert langsmith_qa.sync(again).empty
    assert again.calls == []


def test_sync_dry_run_writes_nothing():
    client = FakeClient()
    plan = langsmith_qa.sync(client, dry_run=True)
    assert len(plan.create) == 30
    assert client.calls == []


def test_the_dataset_command_prints_the_plan(monkeypatch, capsys):
    from tests.eval import run

    client = FakeClient()
    monkeypatch.setattr(run.ls, "Client", lambda: client)
    monkeypatch.setattr(run, "load_env_file", lambda: None)
    run.main(["dataset", "--dry-run"])
    out = capsys.readouterr().out
    assert out.startswith("llm-wiki-qa: would create 30, update 0, delete 0")
    assert client.calls == []


def test_sync_applies_an_edited_question():
    uploaded = [{"id": str(n), **e} for n, e in enumerate(langsmith_qa.examples())]
    uploaded[0] = {**uploaded[0], "outputs": {"answer": "stale", "keys": [["x"]]}}
    client = FakeClient(existing=uploaded)
    langsmith_qa.sync(client)
    assert client.calls == [("update_examples", ["0"])]
