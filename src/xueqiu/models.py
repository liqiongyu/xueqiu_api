from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class XueqiuResponse(BaseModel, Generic[T]):
    """Common Xueqiu response envelope.

    Many endpoints return:
      {"data": ..., "error_code": 0, "error_description": "..."}

    Upstream fields are not stable; we default to accepting extra keys.
    """

    model_config = ConfigDict(extra="allow")

    data: T | None = None
    # Xueqiu sometimes uses `error_code/error_description`, and sometimes `code/message/success`.
    error_code: int = Field(default=0, validation_alias=AliasChoices("error_code", "code"))
    error_description: str | None = Field(
        default=None, validation_alias=AliasChoices("error_description", "message")
    )
    success: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _wrap_raw_payload(cls, data):  # type: ignore[no-untyped-def]
        # Many endpoints return a common envelope, but some return raw objects/lists.
        # If it's not an envelope, wrap it into {"data": ...} so callers can keep a
        # consistent `XueqiuResponse[T]` type.
        if not isinstance(data, dict):
            return {"data": data, "error_code": 0, "error_description": None}

        if any(key in data for key in ("data", "error_code", "code", "success")):
            return data

        return {"data": data, "error_code": 0, "error_description": None}

    @property
    def is_success(self) -> bool:
        if self.success is True:
            return True
        if self.success is False:
            return False
        # Most stock.xueqiu.com endpoints use 0 for success.
        return self.error_code == 0
