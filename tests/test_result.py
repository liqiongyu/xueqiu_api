"""Fetch facts on results: stamping changes nothing else, and copies drop them."""

from __future__ import annotations

import copy
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from xueqiu_api._result import Fetched, FetchFacts, FetchInfo, Rows, info_of, stamp

FETCHED = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
CACHED = FetchInfo(FETCHED, from_cache=True, stale=True)


class Quote(FetchFacts, BaseModel):
    """A result model built like the package's: frozen, keeping undeclared keys."""

    __slots__ = ("_fetch",)
    model_config = ConfigDict(extra="allow", frozen=True)

    symbol: str
    current: float | None = None


@dataclass(frozen=True, slots=True)
class Bars(Fetched):
    symbol: str
    closes: tuple[float, ...]


def quote() -> Quote:
    return Quote.model_validate({"symbol": "SH600000", "current": 10.5, "turnover": 1.2})


def bars() -> Bars:
    return Bars("SH600000", (10.1, 10.2))


def rows() -> Rows[int]:
    return Rows([1, 2, 3])


MAKERS: list[Callable[[], FetchFacts]] = [quote, bars, rows]


def pickled(value: FetchFacts) -> FetchFacts:
    return pickle.loads(pickle.dumps(value))


@pytest.mark.parametrize("make", MAKERS)
def test_an_unstamped_result_has_no_facts(make: Callable[[], FetchFacts]) -> None:
    result = make()
    assert (result.fetched_at, result.from_cache, result.stale) == (None, False, False)
    assert info_of(result) is None


@pytest.mark.parametrize("make", MAKERS)
def test_stamping_sets_the_facts_and_keeps_equality_and_repr(
    make: Callable[[], FetchFacts],
) -> None:
    plain = make()
    stamped = stamp(make(), CACHED)

    assert (stamped.fetched_at, stamped.from_cache, stamped.stale) == (FETCHED, True, True)
    assert info_of(stamped) == CACHED
    assert stamped == plain
    assert repr(stamped) == repr(plain)
    if isinstance(plain, BaseModel):
        assert isinstance(stamped, BaseModel)
        assert stamped.model_dump() == plain.model_dump()
        assert stamped.model_dump_json() == plain.model_dump_json()


@pytest.mark.parametrize("make", MAKERS)
@pytest.mark.parametrize("duplicate", [pickled, copy.copy], ids=["pickle", "copy"])
def test_copies_drop_the_facts(
    make: Callable[[], FetchFacts], duplicate: Callable[[FetchFacts], FetchFacts]
) -> None:
    stamped = stamp(make(), CACHED)
    duplicated = duplicate(stamped)

    assert type(duplicated) is type(stamped)
    assert duplicated == stamped
    assert duplicated.fetched_at is None
    assert (duplicated.from_cache, duplicated.stale) == (False, False)


def test_model_copy_drops_the_facts() -> None:
    stamped = stamp(quote(), CACHED)
    assert stamped.model_copy().fetched_at is None
    assert stamped.model_copy(update={"current": 11.0}).fetched_at is None


def test_rows_are_a_list_and_their_slices_are_plain_lists() -> None:
    stamped = stamp(rows(), FetchInfo(FETCHED))

    assert isinstance(stamped, list)
    assert stamped == [1, 2, 3]
    assert stamped[1:] == [2, 3]
    assert type(stamped[1:]) is list
    assert type(stamped.copy()) is list


def test_a_plain_list_becomes_rows_and_rows_keep_their_object() -> None:
    plain = [1, 2, 3]
    converted = stamp(plain, FetchInfo(FETCHED))
    again = stamp(converted, CACHED)

    assert again is converted
    assert converted is not plain
    assert converted == plain
    assert isinstance(converted, Rows)
    assert info_of(converted) == CACHED


def test_an_upstream_key_named_like_a_fact_stays_in_model_extra() -> None:
    raw = {"symbol": "SH600000", "fetched_at": "2020-01-01", "stale": "yes"}
    model = stamp(Quote.model_validate(raw), FetchInfo(FETCHED))

    assert model.fetched_at == FETCHED
    assert model.stale is False
    assert model.model_extra == {"fetched_at": "2020-01-01", "stale": "yes"}
    assert model.model_dump()["fetched_at"] == "2020-01-01"
    assert Quote.model_validate(raw).fetched_at is None


def test_a_result_without_the_base_cannot_be_stamped() -> None:
    with pytest.raises(TypeError, match="dict"):
        stamp({"symbol": "SH600000"}, FetchInfo(FETCHED))
