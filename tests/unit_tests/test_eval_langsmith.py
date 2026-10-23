"""The Q&A dataset mirrored to LangSmith (0019): planned by code, synced without the network."""

import datetime
import uuid
from types import SimpleNamespace

import langsmith as ls
from langchain_core.messages import AIMessage
from langsmith import schemas

from tests.eval import langsmith_qa, qa_eval
from tests.eval.corpus import load_questions, make_vault

from .scripted_model import ToolScript


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


def test_the_evaluator_is_key_score():
    ref = {"answer": "Jan 5", "keys": [["jan 5"], ["lena"]]}
    assert langsmith_qa.key_facts({"answer": "Lena starts Jan 5."}, ref) == {
        "key": "key_facts",
        "score": 1.0,
    }
    assert langsmith_qa.key_facts({"answer": "In January."}, ref)["score"] == 0.0
    unanswerable = {"answer": "", "keys": "unanswerable"}
    assert (
        langsmith_qa.key_facts({"answer": "The notes don't say."}, unanswerable)[
            "score"
        ]
        == 1.0
    )
    assert langsmith_qa.key_facts(None, ref)["score"] == 0.0  # the target failed


def untraced(run):
    """Turn tracing off inside the target.

    `upload_results=False` sets tracing to "local", but LangChain's tracer inside the
    agent still uploads its runs then; a test must send nothing.
    """

    def inner(inputs):
        with ls.tracing_context(enabled=False):
            return run(inputs)

    return inner


def local_examples(rows):
    """Dataset examples as LangSmith would hand them back, without the network."""
    dataset = uuid.uuid4()
    return [
        schemas.Example(
            id=uuid.uuid4(),
            dataset_id=dataset,
            created_at=datetime.datetime.now(datetime.UTC),
            **row,
        )
        for row in rows
    ]


def test_an_experiment_scores_the_real_assistant_like_the_local_run(
    tmp_path, monkeypatch
):
    """`langsmith.evaluate` offline: real assistant, scripted model, rows for report.json."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "false")
    vault = make_vault(tmp_path / "vault")
    picked = [
        e
        for e in langsmith_qa.examples()
        if e["inputs"]["question"].startswith(
            ("What was the original Atlas GA date", "What is Lena's last name")
        )
    ]
    model = ToolScript(
        messages=iter(
            [
                AIMessage("It was Nov 18."),
                AIMessage("Lena Park."),  # a confident answer to an unanswerable one
                AIMessage("November 18."),
                AIMessage("The notes don't say."),
            ]
        )
    )
    results = langsmith_qa.experiment(
        untraced(langsmith_qa.target(vault, model)),
        data=local_examples(picked),
        repetitions=2,
        upload_results=False,
    )
    rows = langsmith_qa.rows(results)
    assert [(r["q"][:12], r["answer"], r["score"]) for r in rows] == [
        ("What was the", "It was Nov 18.", 1.0),
        ("What is Lena", "Lena Park.", 0.0),
        ("What was the", "November 18.", 1.0),
        ("What is Lena", "The notes don't say.", 1.0),
    ]
    assert [r["unanswerable"] for r in rows] == [False, True, False, True]
    summary = qa_eval.summarize(rows)  # the local report's summary works unchanged
    assert summary["key_facts"] == 0.75 and summary["unanswerable_ok"] == "1/2"
    assert summary["model_calls"] == 4 and summary["failed"] == []


def test_a_failed_question_is_a_row_with_the_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "false")

    def broken(inputs):
        raise ValueError("model down")

    results = langsmith_qa.experiment(
        broken, data=local_examples(langsmith_qa.examples()[:1]), upload_results=False
    )
    [row] = langsmith_qa.rows(results)
    assert row["score"] == 0.0 and "model down" in row["error"]
    assert qa_eval.summarize([row])["failed"] == [row["q"]]


def test_qa_langsmith_writes_the_report_like_a_local_run(tmp_path, monkeypatch, capsys):
    """`run.py qa --langsmith`: the experiment's rows land in report.json."""
    import json

    from tests.eval import run

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "false")
    vault = make_vault(tmp_path / "vault")
    model = ToolScript(messages=iter([AIMessage("It was Nov 18.")]))
    monkeypatch.setattr(run, "configured_model", lambda name: model)
    monkeypatch.setattr(run, "load_env_file", lambda: None)
    real = langsmith_qa.experiment

    def offline(target, *, data=langsmith_qa.DATASET, repetitions, metadata):
        assert data == "llm-wiki-qa" and repetitions == 1
        assert metadata["vault"] == str(vault) and "commit" in metadata["code"]
        return real(
            untraced(target),
            data=local_examples(langsmith_qa.examples()[:1]),
            upload_results=False,
        )

    monkeypatch.setattr(langsmith_qa, "experiment", offline)
    out = tmp_path / "out"
    run.main(["qa", str(vault), "--langsmith", "--out", str(out), "--model", "x:y"])
    report = json.loads((out / "report.json").read_text())
    assert report["meta"]["qa_model"] == "x:y"
    assert report["qa"]["summary"]["key_facts"] == 1.0
    assert report["qa"]["rows"][0]["answer"] == "It was Nov 18."
    assert report["qa"]["experiment"]["repetitions"] == 1
    assert run.headline(report)["qa key facts"] == 1.0
    assert "experiment:" in capsys.readouterr().out
