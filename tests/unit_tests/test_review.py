"""Building the review request and checking answers (pure)."""

import pytest

from llm_wiki.domain.changes import CreateFile, FileDiff
from llm_wiki.domain.review import ReviewPage, ReviewProject, ReviewQuestion, ReviewTask
from llm_wiki.review import (
    approved_digests,
    build_request,
    check_answer,
    revise_feedback,
)


def _diff(path, before):
    return FileDiff(
        path=path,
        before_sha256=before,
        after_text="x\n",
        unified_diff=f"+x ({path})\n",
        edits=(CreateFile(path="05_generated/a.md", text="x\n"),),
    )


DIFFS = [
    _diff("20_people/person-sam-ortiz.md", "a" * 64),
    _diff("10_projects/project-delq.md", None),
    _diff("90_meta/ingest-ledger.json", None),
]
REQUEST = build_request("00_raw/daily/2026-10-12.md", DIFFS, ["w"])


def test_request_lists_files_by_path():
    assert [(f.id, f.path, f.change) for f in REQUEST.files] == [
        (
            "f1",
            "10_projects/project-delq.md",
            "new",
        ),  # by path: IDs stable across rounds
        ("f2", "20_people/person-sam-ortiz.md", "changed"),
        ("f3", "90_meta/ingest-ledger.json", "new"),
    ]
    assert REQUEST.files[1].digest == DIFFS[0].digest
    assert REQUEST.warnings == ("w",)


@pytest.mark.parametrize(
    ("raw", "rejected"),
    [
        ({"action": "approve"}, []),
        ("approve", []),
        (" Approve ", []),
        ({"action": "approve", "reject": ["f2"]}, ["f2"]),
    ],
)
def test_valid_approvals(raw, rejected):
    answer, error = check_answer(REQUEST, raw)
    assert error is None
    expected = [f.digest for f in REQUEST.files if f.id not in rejected]
    assert approved_digests(REQUEST, answer) == expected


def test_only_wiki_pages_are_editable_and_carry_their_text():
    assert [(f.id, f.editable, f.after_text) for f in REQUEST.files] == [
        ("f1", True, "x\n"),
        ("f2", True, "x\n"),
        ("f3", False, ""),  # code-owned json: not editable, text not sent
    ]


def test_revise_rounds_are_counted_down():
    def request(revisions):
        return build_request("n.md", DIFFS, [], revisions=revisions, can_revise=True)

    assert request(0).revisions_left == 3
    answer, error = check_answer(request(2), {"action": "revise", "feedback": "x"})
    assert error is None and answer.feedback == "x"
    _, error = check_answer(request(3), {"action": "revise", "feedback": "x"})
    assert error.startswith("revise limit reached (3)")


def test_edits_and_repeated_warnings_are_shown():
    request = build_request(
        "n.md",
        DIFFS,
        ["w", "w", "v"],
        edited=["20_people/person-sam-ortiz.md", "gone.md"],
    )
    assert request.edited == ("20_people/person-sam-ortiz.md",)  # only shown files
    assert request.warnings == ("w", "v")


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "edit", "file": "f1", "text": "# Delq\n"},
    ],
)
def test_loop_answers_approve_nothing(raw):
    answer, error = check_answer(REQUEST, raw)
    assert error is None
    assert approved_digests(REQUEST, answer) == []


def test_reject_approves_nothing():
    answer, _ = check_answer(REQUEST, "reject")
    assert approved_digests(REQUEST, answer) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"action": "approve", "reject": ["f9"]}, "unknown file ids ['f9']; valid ids"),
        ({"action": "maybe"}, "invalid answer (action:"),
        ({"action": "reject", "reject": ["f1"]}, 'reject not allowed with "reject"'),
        ({"action": "approve", "feedback": "x"}, 'feedback not allowed with "approve"'),
        ({"action": "revise"}, '"revise" needs feedback'),
        ({"action": "edit", "file": "f1"}, '"edit" needs text'),
        ({"action": "make_page", "decisions": ["d1"]}, "invalid answer (action:"),
        ({"action": "edit", "file": "f3", "text": "x"}, "f3 is 90_meta/ingest-ledger"),
        ({"action": "edit", "file": "f7", "text": "x"}, "unknown file ids ['f7']"),
        ({"action": "revise", "feedback": "x"}, "nothing to revise"),
        ({"action": "answer"}, '"answer" needs wins, tasks, rules or aliases'),
        ({"action": "answer", "people": {"p1": "new"}}, "invalid answer (people:"),
        ({"action": "answer", "wins": ["w1"]}, "unknown win ids ['w1']"),
        ({"action": "approve", "note": "hi"}, "invalid answer (note:"),
        (None, "invalid answer"),
    ],
)
def test_bad_answers_are_explained(raw, message):
    answer, error = check_answer(REQUEST, raw)
    assert answer is None
    assert message in error


def test_answer_with_no_wins_is_valid():
    answer, error = check_answer(REQUEST, {"action": "answer", "wins": []})
    assert error is None and answer.wins == ()


def test_task_answers_name_a_project_of_the_review_or_none():
    request = REQUEST.model_copy(
        update={
            "tasks": (ReviewTask(id="m1", line=9, text="Reply to Priya"),),
            "projects": (ReviewProject(slug="project-atlas", title="Atlas"),),
        }
    )
    ok, error = check_answer(request, {"action": "answer", "tasks": {"m1": "none"}})
    assert error is None and ok.tasks == {"m1": "none"}
    ok, error = check_answer(
        request, {"action": "answer", "tasks": {"m1": "project-atlas"}}
    )
    assert error is None
    _, error = check_answer(
        request, {"action": "answer", "tasks": {"m1": "project-zed"}}
    )
    assert "not a project page" in error
    _, error = check_answer(request, {"action": "answer", "tasks": {"m9": "none"}})
    assert error.startswith("unknown task ids")


# --- remember (0009 R3) ------------------------------------------------------------

REMEMBER = build_request(
    "00_raw/daily/2026-10-12.md",
    DIFFS,
    [],
    can_revise=True,
    pages=[ReviewPage(slug="person-sam-ortiz", title="Sam Ortiz", names=("Sam",))],
    rules=["Old rule"],
    aliases={"Ortiz": "person-sam-ortiz"},
)


def test_request_carries_what_is_remembered():
    assert REMEMBER.rules == ("Old rule",)
    assert [(a.name, a.slug) for a in REMEMBER.aliases] == [
        ("Ortiz", "person-sam-ortiz")
    ]


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "answer", "rules": ["Old rule", "Skip lunch plans"]},
        {"action": "answer", "rules": []},  # forget them all
        {"action": "answer", "aliases": {"Sammy O": "person-sam-ortiz"}},
        {"action": "answer", "aliases": {}},
        {"action": "revise", "feedback": "Skip lunch plans", "remember": True},
    ],
)
def test_valid_remember_answers(raw):
    assert check_answer(REMEMBER, raw)[1] is None


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"action": "answer", "rules": [" "]}, "a rule is empty"),
        (
            {"action": "answer", "aliases": {"Sam": "person-sam-ortiz"}},
            "already a name",
        ),
        ({"action": "answer", "aliases": {"Zed": "project-zed"}}, "is not a page"),
        (
            {"action": "edit", "file": "f1", "text": "x", "rules": ["a"]},
            "rules not allowed",
        ),
        ({"action": "approve", "remember": True}, "remember not allowed"),
    ],
)
def test_bad_remember_answers(raw, error):
    assert error in check_answer(REMEMBER, raw)[1]


# --- the agent's questions ---------------------------------------------------------

ASKED = build_request(
    "00_raw/daily/2026-10-12.md",
    DIFFS,
    [],
    can_revise=True,
    questions=[
        ReviewQuestion(id="q1", line=9, quote="Add jfrog CI", text="Which project?"),
        ReviewQuestion(id="q2", line=12, quote="Ask Eugene", text="Who is Eugene?"),
    ],
)


def test_answers_become_the_feedback_the_agent_reads():
    answer, error = check_answer(
        ASKED,
        {
            "action": "revise",
            "answers": {"q1": "Risk  analytics\nwork"},
            "feedback": "Also skip lunch.",
        },
    )
    assert error is None
    assert revise_feedback(ASKED, answer) == (
        'Line 9 ("Add jfrog CI") you asked: Which project? Answer: Risk analytics work\n'
        "Also skip lunch."
    )


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"action": "revise", "answers": {"q9": "x"}}, "unknown question ids ['q9']"),
        ({"action": "revise", "answers": {"q1": "  "}}, "q1: the answer is empty"),
        ({"action": "revise", "answers": {"q1": "x" * 501}}, "at most 500"),
        (
            {"action": "revise", "answers": {"q1": "x"}, "remember": True},
            "remember keeps the feedback",
        ),
        ({"action": "answer", "answers": {"q1": "x"}}, "answers not allowed"),
    ],
)
def test_bad_question_answers(raw, error):
    assert error in check_answer(ASKED, raw)[1]


# --- bookkeeping is all or nothing (decision 0013) ---------------------------------

BOOKS = [
    "00_raw/daily/2026-10-12.md",
    "05_generated/open-tasks.md",
    "90_meta/ingest-ledger.json",
    "90_meta/tasks.json",
]
WITH_BOOKS = build_request(
    "00_raw/daily/2026-10-12.md",
    [_diff(p, None) for p in [*BOOKS, "30_decisions/2026/decisions-2026-q4.md"]],
    [],
)


@pytest.mark.parametrize("path", BOOKS)
def test_bookkeeping_cannot_be_left_out(path):
    (f,) = [f for f in WITH_BOOKS.files if f.path == path]
    assert f.required and not f.editable
    error = check_answer(WITH_BOOKS, {"action": "approve", "reject": [f.id]})[1]
    assert f"{f.id} is {path}" in error and "go together" in error


def test_wiki_pages_can_still_be_left_out_and_bookkeeping_alone_is_fine():
    (log,) = [f for f in WITH_BOOKS.files if not f.required]
    answer, error = check_answer(WITH_BOOKS, {"action": "approve", "reject": [log.id]})
    assert error is None
    assert len(approved_digests(WITH_BOOKS, answer)) == len(BOOKS)
