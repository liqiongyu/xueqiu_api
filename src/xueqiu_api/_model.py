"""Shared result and params models.

Result models inherit ``UpstreamModel``, so that the keys naming a user never reach them, not even
inside nested values.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from ._values import Number

USER_KEY = re.compile(
    r"^(users?|uid|user_?id|screen_name|nick_?name|avatar\w*|profile_image\w*|comments?"
    r"|owner\w*|contractor\w*)$",
    re.IGNORECASE,
)
"""Upstream keys that name a user; never kept in a result, a fixture or a model field."""

Order = Literal["asc", "desc"]
"""Sort direction of a list parameter."""


class NoParams(BaseModel):
    """Params of an endpoint that takes none."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _without_keys(value: Any, drop: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_keys(item, drop)
            for key, item in cast(dict[Any, Any], value).items()
            if not (key in drop or (isinstance(key, str) and USER_KEY.match(key)))
        }
    if isinstance(value, list):
        return [_without_keys(item, drop) for item in cast(list[Any], value)]
    return value


class UpstreamModel(BaseModel):
    """A result model. Fields the model does not declare stay in ``model_extra``.

    Before validation, keys matching ``USER_KEY`` or listed in ``drop`` are removed at every depth.
    """

    model_config = ConfigDict(extra="allow", frozen=True, use_attribute_docstrings=True)

    drop: ClassVar[frozenset[str]] = frozenset()
    """Other upstream keys to remove, such as prompts and UI fields."""

    @model_validator(mode="before")
    @classmethod
    def _drop_keys(cls, value: Any) -> Any:
        return _without_keys(value, cls.drop)


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
