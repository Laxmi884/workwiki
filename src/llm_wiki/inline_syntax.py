"""Obsidian inline syntax inside one line of text: `[[links]]` and `#tags` (decision 0002)."""

from __future__ import annotations

import re

# `inline code` spans: their contents are never links or tags.
_CODE_RE = re.compile(r"`[^`]*`")

# [[target]], [[target|alias]], [[target#heading]], [[target^block]]; not ![[embeds]].
_LINK_RE = re.compile(r"(?<!!)\[\[(?P<target>[^\[\]|#^]*)[^\[\]]*\]\]")

# #tag: not after a word char, "#", "/" or "&" (rules out a#b, ##, URLs, &#39;).
_TAG_RE = re.compile(r"(?<![\w#/&])#(?P<name>[\w/-]+)")

# A tag needs at least one letter or "_": "#42" is an issue number, not a tag.
_HAS_LETTER_RE = re.compile(r"[^\W\d]")


def _without_code(text: str) -> str:
    return _CODE_RE.sub(" ", text)


def plain_text(text: str) -> str:
    """Return `text` with inline code and `[[links]]` blanked out.

    Use it before searching prose, so "atlas" inside "[[project-atlas]]" or
    "#tag" inside "[[a#tag]]" is not found.
    """
    return _LINK_RE.sub(" ", _without_code(text))


def _unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))  # dedupe, keep first-seen order


def find_links(text: str) -> tuple[str, ...]:
    """Return link targets in order, without alias or heading part, each once.

    "[[person-sam|Sam]] and [[project-atlas#Risks]]" -> ("person-sam", "project-atlas")
    """
    targets = (
        m.group("target").strip() for m in _LINK_RE.finditer(_without_code(text))
    )
    return _unique([t for t in targets if t])


def find_tags(text: str) -> tuple[str, ...]:
    """Return tags in order, lowercased, without "#", each once.

    "Fixed it #win #Win, see #42" -> ("win",)
    """
    names = (m.group("name").lower() for m in _TAG_RE.finditer(plain_text(text)))
    return _unique([n for n in names if _HAS_LETTER_RE.search(n)])
