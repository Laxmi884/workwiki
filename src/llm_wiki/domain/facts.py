"""Facts the model extracts from one daily-note section (phase 2).

These are model-facing schemas: class docstrings and field descriptions are sent to the
model as part of the prompt, so they say what to extract and what to leave out.

- People, projects and topics are given as written ("Sam") or as a slug
  ("person-sam-ortiz"); code resolves them to slugs. A bad name never fails parsing.
- `line` is the file line number the model is shown next to each line of the section,
  so code can link every fact to its source and check `#win`/`#decision` coverage.
- Optional values stay None unless the note states them (no invented numbers).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    line: int = Field(
        ge=1, description="Line number (shown before each line) the fact comes from."
    )


class Decision(_Fact):
    """A decision that was made. Not an idea, a proposal or an open question."""

    title: str = Field(description="Short name, e.g. 'Keep old BI tool until Q1'.")
    rationale: str | None = Field(
        default=None, description="Why it was decided, only if the note says so."
    )
    project: str | None = Field(
        default=None,
        description="Project slug (e.g. 'project-atlas') or name; null if none.",
    )
    people: list[str] = Field(
        default_factory=list,
        description="People involved: slugs (e.g. 'person-alex-chen') or names.",
    )


class Win(_Fact):
    """An achievement worth remembering for a performance review."""

    text: str = Field(description="What was achieved, in one sentence.")
    project: str | None = Field(
        default=None, description="Project slug or name; null if none."
    )


class PersonNote(_Fact):
    """Something worth remembering about a person (goals, preferences, context)."""

    person: str = Field(description="Person slug (e.g. 'person-sam-ortiz') or name.")
    note: str = Field(description="The fact about them, in one sentence.")


class Help(_Fact):
    """Help the author gave someone."""

    person: str = Field(description="Who was helped: slug or name.")
    what: str = Field(description="What the help was, in one sentence.")
    minutes: int | None = Field(
        default=None,
        ge=1,
        description="Duration in minutes, only if written in the note; never estimate.",
    )


class Learning(_Fact):
    """Something the author learned."""

    topic: str = Field(
        description="Concept slug (e.g. 'concept-feature-flags') or a short topic name."
    )
    summary: str = Field(description="What was learned, in one sentence.")


class ProjectUpdate(_Fact):
    """Progress, a problem or a change of plan on a project (not a task)."""

    project: str = Field(description="Project slug or name.")
    note: str = Field(description="The update, in one sentence.")


class SectionFacts(BaseModel):
    """Everything worth keeping from one section. Leave a list empty when nothing fits.

    Do not extract tasks (checkbox lines); code handles them.
    """

    model_config = ConfigDict(frozen=True)

    decisions: list[Decision] = Field(default_factory=list)
    wins: list[Win] = Field(default_factory=list)
    person_notes: list[PersonNote] = Field(default_factory=list)
    helps: list[Help] = Field(default_factory=list)
    learnings: list[Learning] = Field(default_factory=list)
    project_updates: list[ProjectUpdate] = Field(default_factory=list)

    def cited_lines(self) -> set[int]:
        """Return the line numbers any fact cites (used to check `#win`/`#decision` lines)."""
        facts: list[_Fact] = [
            *self.decisions,
            *self.wins,
            *self.person_notes,
            *self.helps,
            *self.learnings,
            *self.project_updates,
        ]
        return {fact.line for fact in facts}
