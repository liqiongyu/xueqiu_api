"""Shared result and params models.

Result models inherit ``UpstreamModel``, so that the keys naming a user never reach them, not even
inside nested values.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from ._result import FetchFacts
from ._values import Number

USER_KEY = re.compile(
    r"user(?!_rb)|(^|_)uid(_|$)|screen_?name|nick_?name|avatar|profile_image|photo_domain|comment"
    r"|retweet|(^|_)owner|(^|_)contractor",
    re.IGNORECASE,
)
"""Upstream keys that name a user or carry a post, under any prefix or suffix; never kept in a
result, a fixture or a model field. ``last_user_rb_gid`` is a rebalancing id, so it stays."""

Order = Literal["asc", "desc"]
"""Sort direction of a list parameter."""


# Params of an endpoint that takes none.
class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def without_keys(value: Any, drop: frozenset[str] = frozenset()) -> Any:
    """``value`` without the keys matching ``USER_KEY`` or listed in ``drop``, at every depth."""
    if isinstance(value, dict):
        return {
            key: without_keys(item, drop)
            for key, item in cast(dict[Any, Any], value).items()
            if not (key in drop or (isinstance(key, str) and USER_KEY.search(key)))
        }
    if isinstance(value, list):
        return [without_keys(item, drop) for item in cast(list[Any], value)]
    return value


class UpstreamModel(FetchFacts, BaseModel):
    """A result model. Fields the model does not declare stay in ``model_extra``.

    Before validation, keys matching ``USER_KEY`` or listed in ``drop`` are removed at every depth.
    A model a client returns carries fetch facts; see ``FetchFacts``.
    """

    __slots__ = ("_fetch",)
    model_config = ConfigDict(extra="allow", frozen=True, use_attribute_docstrings=True)

    drop: ClassVar[frozenset[str]] = frozenset()
    """Other upstream keys to remove, such as prompts and UI fields."""

    @model_validator(mode="before")
    @classmethod
    def _drop_keys(cls, value: Any) -> Any:
        return without_keys(value, cls.drop)


class UnverifiedModel(UpstreamModel):
    """A result model of an endpoint no real response was ever seen from.

    The shape is second-hand, so what else the upstream sends is unknown and nothing undeclared
    is kept.
    """

    model_config = ConfigDict(extra="ignore")


class QuoteRow(UpstreamModel):
    """One security in a list of quotes; the endpoint models add their own fields."""

    symbol: str = Field(validation_alias=AliasChoices("symbol", "code"))
    """Xueqiu symbol (upstream ``symbol`` or ``code``)."""
    name: str | None = None
    current: Number = None
    """Last price, in the quote currency."""
    chg: Number = Field(default=None, validation_alias=AliasChoices("chg", "change"))
    """Change from the previous close, in the quote currency."""
    percent: Number = Field(default=None, validation_alias=AliasChoices("percent", "percentage"))
    """Change from the previous close, in percent points (1.23 means 1.23%)."""
