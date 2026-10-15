"""Building the review request and checking answers (pure)."""

import pytest

from llm_wiki.domain.changes import AppendToSection, CreateFile, FileDiff
from llm_wiki.domain.facts import Decision, SectionFacts
from llm_wiki.review import approved_digests, build_request, check_answer
from llm_wiki.routing import INBOX_PATH


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
    _diff("05_generated/inbox.md", None),
    _diff("90_meta/ingest-ledger.json", None),
]
FACTS = {
    "32": SectionFacts(decisions=[Decision(line=35, title="Ship on Friday")]),
    "20": SectionFacts(decisions=[Decision(line=23, title="Keep the old BI tool")]),
}
INBOX_EDIT = AppendToSection(
    path=INBOX_PATH,
    heading="## 2026-10-12",
    lines=("- 2026-10-12: learning (Cookies): Must match. ([[2026-10-12]])",),
)
REQUEST = build_request("00_raw/daily/2026-10-12.md", DIFFS, FACTS, [INBOX_EDIT], ["w"])


def test_request_lists_files_decisions_and_inbox():
    assert [(f.id, f.path, f.change) for f in REQUEST.files] == [
        ("f1", "05_generated/inbox.md", "new"),  # by path: IDs stable across rounds
        ("f2", "20_people/person-sam-ortiz.md", "changed"),
        ("f3", "90_meta/ingest-ledger.json", "new"),
    ]
    assert REQUEST.files[1].digest == DIFFS[0].digest
    assert [(d.id, d.title) for d in REQUEST.decisions] == [
        ("d1", "Keep the old BI tool"),  # note order, not dict order
        ("d2", "Ship on Friday"),
    ]
    assert [i.text for i in REQUEST.inbox] == [
        "2026-10-12: learning (Cookies): Must match. ([[2026-10-12]])"
    ]
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
        return build_request(
            "n.md", DIFFS, FACTS, [], [], revisions=revisions, can_revise=True
        )

    assert request(0).revisions_left == 3
    answer, error = check_answer(request(2), {"action": "revise", "feedback": "x"})
    assert error is None and answer.feedback == "x"
    _, error = check_answer(request(3), {"action": "revise", "feedback": "x"})
    assert error.startswith("revise limit reached (3)")


def test_decision_pages_edits_and_repeated_warnings_are_shown():
    request = build_request(
        "n.md",
        DIFFS,
        FACTS,
        [],
        ["w", "w", "v"],
        decision_pages=[35],
        edited=["20_people/person-sam-ortiz.md", "gone.md"],
    )
    assert [(d.id, d.page) for d in request.decisions] == [("d1", False), ("d2", True)]
    assert request.edited == ("20_people/person-sam-ortiz.md",)  # only shown files
    assert request.warnings == ("w", "v")


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "make_page", "decisions": ["d1"]},
        {"action": "edit", "file": "f1", "text": "# Sam\n"},
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
        ({"action": "make_page", "decisions": ["d9"]}, "unknown decision ids ['d9']"),
        ({"action": "edit", "file": "f3", "text": "x"}, "f3 is 90_meta/ingest-ledger"),
        ({"action": "edit", "file": "f7", "text": "x"}, "unknown file ids ['f7']"),
        ({"action": "revise", "feedback": "x"}, "nothing to revise"),
        ({"action": "approve", "note": "hi"}, "invalid answer (note:"),
        (None, "invalid answer"),
    ],
)
def test_bad_answers_are_explained(raw, message):
    answer, error = check_answer(REQUEST, raw)
    assert answer is None
    assert message in error
