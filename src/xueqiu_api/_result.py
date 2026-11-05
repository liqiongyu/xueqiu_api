"""Facts about the fetch behind a result: when the upstream answered, and whether the result
came from the cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FetchInfo:
    """How one result was fetched."""

    fetched_at: datetime
    """When the upstream answered, timezone-aware."""
    from_cache: bool = False
    stale: bool = False
    """Served from the cache past its freshness because the upstream failed."""


class FetchFacts:
    """Read-only facts about the fetch behind a result: ``fetched_at``, ``from_cache`` and
    ``stale``. A result that was not fetched, such as one built directly, reads None, False
    and False.

    The facts describe one fetch, so ``pickle``, ``copy.copy`` and pydantic's ``model_copy``
    drop them on every result type. On a model, an upstream key named like a fact stays in
    ``model_extra``, while the attribute gives the fact.

    Subclasses declare ``__slots__ = ("_fetch",)``.
    """

    __slots__ = ()

    @property
    def fetched_at(self) -> datetime | None:
        """When the upstream answered; for a cached result, the first fetch."""
        info = info_of(self)
        return None if info is None else info.fetched_at

    @property
    def from_cache(self) -> bool:
        info = info_of(self)
        return info is not None and info.from_cache

    @property
    def stale(self) -> bool:
        """Whether the result is past its freshness, served because the upstream failed."""
        info = info_of(self)
        return info is not None and info.stale


class Fetched(FetchFacts):
    """Base of the dataclass results; see ``FetchFacts``."""

    __slots__ = ("_fetch",)


class Rows(FetchFacts, list[T]):
    """A list result with fetch facts; see ``FetchFacts``. Slices and other list operations
    give plain lists."""

    __slots__ = ("_fetch",)

    def __reduce__(self) -> tuple[type[Rows[Any]], tuple[list[T]]]:
        return (Rows, (list(self),))


def stamp(result: T, info: FetchInfo) -> T:
    """``result`` carrying ``info``. A plain list becomes ``Rows``; any other result must be
    a ``FetchFacts``."""
    target: object = result
    if type(target) is list:
        target = Rows(cast(list[Any], target))
    if not isinstance(target, FetchFacts):
        raise TypeError(f"a {type(target).__name__} result cannot carry fetch facts")
    object.__setattr__(target, "_fetch", info)
    return cast(T, target)


def info_of(result: object) -> FetchInfo | None:
    """The fetch facts ``result`` carries, or None."""
    info = getattr(result, "_fetch", None) if isinstance(result, FetchFacts) else None
    return info if isinstance(info, FetchInfo) else None
