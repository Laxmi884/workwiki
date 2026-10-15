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

MAX_PER_KIND = 20  # facts of one kind from one section


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

    text: str = Field(
        description="What was achieved. Keep the specifics the note gives (names, dates, numbers, reasons); one to three sentences."
    )
    project: str | None = Field(
        default=None, description="Project slug or name; null if none."
    )


class PersonNote(_Fact):
    """Something worth remembering about a person (goals, preferences, context)."""

    person: str = Field(description="Person slug (e.g. 'person-sam-ortiz') or name.")
    note: str = Field(
        description="The fact about them. Keep the specifics the note gives (names, dates, numbers, reasons); one to three sentences."
    )


class Help(_Fact):
    """Help the author gave someone."""

    person: str = Field(description="Who was helped: slug or name.")
    what: str = Field(
        description="What the help was. Keep the specifics the note gives (names, dates, numbers, reasons); one to three sentences."
    )
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
    summary: str = Field(
        description="What was learned. Keep the specifics the note gives (names, dates, numbers, reasons); one to three sentences."
    )


class ProjectUpdate(_Fact):
    """Progress, a problem or a change of plan on a project (not a task)."""

    project: str = Field(description="Project slug or name.")
    note: str = Field(
        description="The update. Keep the specifics the note gives (names, dates, numbers, reasons); one to three sentences."
    )


class SectionFacts(BaseModel):
    """Everything worth keeping from one section. Leave a list empty when nothing fits.

    Do not extract tasks (checkbox lines); code handles them.
    """

    model_config = ConfigDict(frozen=True)

    # max_length becomes maxItems in the JSON schema: local models decoding against
    # it must close the list, instead of repeating items until the token limit.
    decisions: list[Decision] = Field(default_factory=list, max_length=MAX_PER_KIND)
    wins: list[Win] = Field(default_factory=list, max_length=MAX_PER_KIND)
    person_notes: list[PersonNote] = Field(
        default_factory=list, max_length=MAX_PER_KIND
    )
    helps: list[Help] = Field(default_factory=list, max_length=MAX_PER_KIND)
    learnings: list[Learning] = Field(default_factory=list, max_length=MAX_PER_KIND)
    project_updates: list[ProjectUpdate] = Field(
        default_factory=list, max_length=MAX_PER_KIND
    )

    def person_names(self) -> list[tuple[int, str]]:
        """Return (line, name) for every person a fact names, in fact order."""
        found = [(d.line, p) for d in self.decisions for p in d.people]
        found += [(p.line, p.person) for p in self.person_notes]
        found += [(h.line, h.person) for h in self.helps]
        return found

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


# --- names and projects in task lines (review questions, phase 3c-2) ---------------


class PersonMention(BaseModel):
    """A person named in the lines: a human, not a tool, system, team or acronym."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="The name exactly as written, e.g. 'Eugene'.")
    lines: list[int] = Field(
        min_length=1, description="Line numbers (shown before each line) naming them."
    )


class ProjectGroup(BaseModel):
    """Tasks that belong to the same piece of work, with a short name for it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        description="Short project name taken from the task wording, e.g. "
        "'Loss History'. Use a known project slug if one fits."
    )
    lines: list[int] = Field(
        min_length=1, description="Line numbers of the tasks in this project."
    )


class NameScan(BaseModel):
    """People named in the lines, and unlinked tasks grouped into projects.

    Leave a list empty when nothing fits.
    """

    model_config = ConfigDict(frozen=True)

    people: list[PersonMention] = Field(default_factory=list, max_length=MAX_PER_KIND)
    projects: list[ProjectGroup] = Field(default_factory=list, max_length=MAX_PER_KIND)
