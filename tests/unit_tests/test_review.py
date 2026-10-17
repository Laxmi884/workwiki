"""Building the review request and checking answers (pure)."""

import pytest

from llm_wiki.domain.changes import CreateFile, FileDiff
from llm_wiki.domain.review import ReviewProject, ReviewTask
from llm_wiki.review import approved_digests, build_request, check_answer


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
        ({"action": "answer"}, '"answer" needs wins or tasks'),
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
