"""Local search over the registry's own texts: BM25 on CJK bigrams and ASCII words.

It needs nothing from the ``mcp`` extra. A full scan is enough for a registry of fewer than 200
endpoints.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ._synonyms import SYNONYMS
from .registry import Endpoint

RESULT_CAP = 10
"""Most hits of one search."""

_K1 = 1.2
_B = 0.25
_TITLE_WEIGHT = 5.0
_KEYWORD_WEIGHT = 4.0
_NAME_WEIGHT = 3.0
_SUMMARY_WEIGHT = 1.0
_SYNONYM_WEIGHT = 0.7
_RELATIVE_FLOOR = 0.2
"""Hits scoring under this share of the best score are dropped."""
_SCORE_DIGITS = 6
"""Rounding that keeps a last-bit difference between platforms from changing the order."""

_UNIT = re.compile(r"[㐀-䶿一-鿿]|[a-z0-9]+")
_JOINED_ASCII_CHARS = 3
"""An ASCII word this short joins the CJK next to it, as in k线 and 证50."""


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


_STOP_WORDS = (
    "a an the of for to in on at by is are was were be do does did what which who whom when how "
    "much many me my i you it its this that and or with from get show list please can give about "
    "right now"
)
_SITE_NAMES = "雪球 蛋卷 xueqiu snowball danjuan"
"""They barely tell endpoints apart, and a question about posts on a site should find nothing."""
_QUERY_STOP = frozenset(_stem(word) for word in f"{_STOP_WORDS} {_SITE_NAMES}".split())


def _joins(left: str, right: str) -> bool:
    if left.isascii() == right.isascii():
        return not left.isascii()
    return len(left if left.isascii() else right) <= _JOINED_ASCII_CHARS


def terms(text: str, *, query: bool = False) -> list[str]:
    """Search terms of ``text``: ASCII words, CJK bigrams and short words joined to CJK.

    A query also loses its stop words; both sides of a match otherwise split text the same way.
    """
    units = [_stem(u) if u.isascii() else u for u in _UNIT.findall(_normal(text))]
    found: list[str] = []
    for index, unit in enumerate(units):
        if unit.isascii():
            found.append(unit)
        if index + 1 < len(units) and _joins(unit, units[index + 1]):
            found.append(unit + units[index + 1])
    return [term for term in found if not (query and term in _QUERY_STOP)]


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """A CJK alias matches anywhere in a query, an ASCII one only as whole words."""
    if alias.isascii():
        return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")
    return re.compile(re.escape(alias))


def _weighted_fields(spec: Endpoint[Any, Any]) -> Iterable[tuple[float, str]]:
    agent = spec.agent
    return (
        (_TITLE_WEIGHT, agent.title_zh),
        (_TITLE_WEIGHT, agent.title_en),
        # Each keyword on its own: no bigram spans two keywords.
        *((_KEYWORD_WEIGHT, keyword) for keyword in agent.keywords),
        (_NAME_WEIGHT, spec.name),
        (_SUMMARY_WEIGHT, agent.summary),
    )


@dataclass(frozen=True, slots=True)
class Hit:
    name: str
    score: float


class EndpointIndex:
    """Built once, then read-only, so one instance is safe across threads and on 3.14t."""

    def __init__(
        self,
        specs: Iterable[Endpoint[Any, Any]],
        *,
        synonyms: Mapping[str, tuple[str, ...]] = SYNONYMS,
    ) -> None:
        self._frequencies: dict[str, dict[str, float]] = {}
        self._lengths: dict[str, int] = {}
        self._exact: dict[str, str] = {}
        for spec in specs:
            frequencies: defaultdict[str, float] = defaultdict(float)
            length = 0
            for weight, text in _weighted_fields(spec):
                for term in terms(text):
                    frequencies[term] += weight
                    length += 1
            self._frequencies[spec.name] = dict(frequencies)
            self._lengths[spec.name] = length
            for text in (spec.name, spec.agent.title_zh, spec.agent.title_en):
                self._exact.setdefault(_normal(text), spec.name)
        count = len(self._frequencies)
        found = Counter(term for terms_of in self._frequencies.values() for term in terms_of)
        self._idf = {
            term: math.log(1 + (count - seen + 0.5) / (seen + 0.5)) for term, seen in found.items()
        }
        self._average_length = sum(self._lengths.values()) / max(count, 1)
        self._aliases = [
            (_alias_pattern(alias), terms(canonical))
            for canonical, aliases in synonyms.items()
            for alias in aliases
        ]

    def search(self, query: str, *, only: Collection[str] | None = None) -> list[Hit]:
        """Best endpoints first, at most ``RESULT_CAP``, among ``only`` when given.

        A query equal to an endpoint's name or a title puts that endpoint first; its score does
        not count as the best one. The rest go by score, then by name.
        """
        weights = self._weights(query)
        scores = {
            name: self._score(name, weights)
            for name in self._frequencies
            if only is None or name in only
        }
        exact = self._exact.get(_normal(query).strip())
        first = [Hit(exact, scores.pop(exact))] if exact is not None and exact in scores else []
        floor = max(scores.values(), default=0.0) * _RELATIVE_FLOOR
        rest = [Hit(name, s) for name, s in scores.items() if s > 0 and s >= floor]
        rest.sort(key=lambda hit: (-hit.score, hit.name))
        return [*first, *rest][:RESULT_CAP]

    def _weights(self, query: str) -> dict[str, float]:
        weights = dict.fromkeys(terms(query, query=True), 1.0)
        text = _normal(query)
        for pattern, canonical in self._aliases:
            if pattern.search(text):
                for term in canonical:
                    weights.setdefault(term, _SYNONYM_WEIGHT)
        return weights

    def _score(self, name: str, weights: Mapping[str, float]) -> float:
        frequencies = self._frequencies[name]
        saturation = _K1 * (1 - _B + _B * self._lengths[name] / self._average_length)
        return round(
            sum(
                weight
                * self._idf[term]
                * frequencies[term]
                * (_K1 + 1)
                / (frequencies[term] + saturation)
                for term, weight in weights.items()
                if term in frequencies
            ),
            _SCORE_DIGITS,
        )
