"""f10_company: the company profile of one listed company."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from pydantic import AliasChoices, Field, ValidationInfo, field_validator

from .._f10 import region_query
from .._model import UpstreamModel
from .._time import date_from_ms
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, exchange_timezone


def _named(name: str, hong_kong: str) -> Any:
    return Field(default=None, validation_alias=AliasChoices(name, hong_kong))


class Company(UpstreamModel):
    """Company profile. Field names follow the A-share and US responses.

    Hong Kong responses use other names; the ones with a clear counterpart are read into the
    fields below (``comcnname``, ``comenname``, ``mbu``, ``comintr``, ``incdate``,
    ``lsdateipo``, ``web_site``). Every other upstream field is kept in ``model_extra``.
    """

    symbol: str
    """The requested symbol."""
    org_name_cn: str | None = _named("org_name_cn", "comcnname")
    """Full Chinese name."""
    org_short_name_cn: str | None = None
    """Short Chinese name; not sent for Hong Kong."""
    org_name_en: str | None = _named("org_name_en", "comenname")
    main_operation_business: str | None = _named("main_operation_business", "mbu")
    """Main business: upstream free text."""
    org_cn_introduction: str | None = _named("org_cn_introduction", "comintr")
    """Company introduction: upstream free text."""
    established_date: date | None = _named("established_date", "incdate")
    """Founding date (incorporation date for Hong Kong), in the exchange timezone."""
    listed_date: date | None = _named("listed_date", "lsdateipo")
    """Listing date, in the exchange timezone."""
    staff_num: int | None = None
    """Number of employees."""
    chairman: str | None = None
    org_website: str | None = _named("org_website", "web_site")

    @field_validator("established_date", "listed_date", mode="before")
    @classmethod
    def _exchange_date(cls, value: Any, info: ValidationInfo) -> Any:
        return date_from_ms(value, exchange_timezone(info.data.get("symbol")))


def _parse(payload: Any, params: SymbolParams) -> Company:
    company: Any = (
        cast(dict[str, Any], payload).get("company") if isinstance(payload, dict) else None
    )
    if not isinstance(company, dict):
        raise ValueError("f10 company data needs a company object")
    return Company.model_validate({**cast(dict[str, Any], company), "symbol": params.symbol})


ENDPOINT = Endpoint(
    name="f10_company",
    doc="Company profile of one listed company.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/{region}/company.json",
    params=SymbolParams,
    build_query=region_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK", "US"),
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="company",
        title_zh="公司资料",
        title_en="Company profile",
        summary=(
            "取 A 股、港股或美股上市公司的基本资料，包括全称、英文名、成立和上市日期、"
            "员工数、董事长和公司简介。"
        ),
        keywords=(
            "公司资料",
            "公司简介",
            "主营业务",
            "上市日期",
            "董事长",
            "F10",
            "company profile",
            "company info",
            "listing date",
        ),
        default_fields=(
            "org_name_cn",
            "org_short_name_cn",
            "org_name_en",
            "established_date",
            "listed_date",
            "staff_num",
            "chairman",
            "org_website",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
        free_text=("main_operation_business", "org_cn_introduction", "operating_scope"),
    ),
)
