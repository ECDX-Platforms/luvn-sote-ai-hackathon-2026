"""Tokenising and stem matching for Finnish prose, shared by retrieval and the conversational surface.

These three functions used to live in `chat.py`, where `knowledge_forms` imported them privately. They are
the only text matchers that read record or form text, and they are deliberately crude: a stem comparison
cannot be prompt-injected into agreement, which is why no cleverer matcher replaces them.
"""

from __future__ import annotations

import re

WORD = re.compile(r"[a-zä-öå0-9]+")


def tokens(text: str) -> set[str]:
    return set(WORD.findall((text or "").lower()))


def common_prefix(left: str, right: str) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def overlap(left: set[str], right: set[str]) -> int:
    """How many terms of `left` have a stem in `right`.

    Finnish inflects heavily, so an exact set intersection finds almost nothing: a question about
    "lääkityksestä" shares no token with a record about "lääkitys". Two terms are treated as the same stem when
    they agree on five or more leading characters, or on all but the last two of a shorter term. This is a
    heuristic and it is deliberately the *only* matcher here that reads the record text, because a stem
    comparison cannot be prompt-injected into agreement.
    """
    matches = 0
    for term in left:
        if len(term) < 4:
            continue
        for other in right:
            if len(other) < 4:
                continue
            if common_prefix(term, other) >= min(5, len(term) - 1, len(other) - 1):
                matches += 1
                break
    return matches
