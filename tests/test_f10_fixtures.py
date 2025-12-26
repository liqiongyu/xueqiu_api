from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from xueqiu.api.f10 import (
    F10BonusData,
    F10BusinessAnalysisData,
    F10IndustryCompareData,
    F10IndustryData,
    F10MainIndicatorData,
    F10OrgHoldingChangeData,
    F10ShareholderCountData,
    F10SharesChangeData,
    F10SkholderChangeData,
    F10SkholderData,
    F10TopHoldersData,
)
from xueqiu.models import XueqiuResponse


def test_f10_fixtures_parse_when_present() -> None:
    fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "f10"
    paths = sorted(p for p in fixtures_dir.glob("*__*.json") if p.is_file())
    if not paths:
        pytest.skip("No real fixtures found; run `uv run python scripts/fetch_f10_fixtures.py`.")

    models = {
        "industry": XueqiuResponse[F10IndustryData],
        "business_analysis": XueqiuResponse[F10BusinessAnalysisData],
        "skholder": XueqiuResponse[F10SkholderData],
        "skholderchg": XueqiuResponse[F10SkholderChangeData],
        "shareschg": XueqiuResponse[F10SharesChangeData],
        "holders": XueqiuResponse[F10ShareholderCountData],
        "org_holding_change": XueqiuResponse[F10OrgHoldingChangeData],
        "bonus": XueqiuResponse[F10BonusData],
        "indicator": XueqiuResponse[F10MainIndicatorData],
        "industry_compare": XueqiuResponse[F10IndustryCompareData],
        "top_holders": XueqiuResponse[F10TopHoldersData],
    }

    for path in paths:
        endpoint = path.stem.split("__", 1)[1]
        model = models.get(endpoint)
        assert model is not None, f"Unknown fixture endpoint {endpoint!r} in {path.name}"

        payload = json.loads(path.read_text(encoding="utf-8"))
        parsed = TypeAdapter(model).validate_python(payload)
        assert parsed.is_success is True
        assert parsed.data is not None

        if endpoint == "industry":
            assert parsed.data.company is not None
            assert isinstance(parsed.data.company.listed_at, datetime)
            assert parsed.data.company.listed_at.tzinfo is not None
            assert parsed.data.industries
            assert parsed.data.industries[0].code
            assert parsed.data.industries[0].name

        if endpoint == "business_analysis":
            assert parsed.data.items
            assert parsed.data.items[0].report_name
            assert parsed.data.items[0].operating_analysis_explain

        if endpoint == "skholder":
            assert parsed.data.items
            assert isinstance(parsed.data.items[0].employment_start, datetime)
            assert parsed.data.items[0].employment_start.tzinfo is not None

        if endpoint == "skholderchg":
            assert parsed.data.items
            assert isinstance(parsed.data.items[0].change_date, datetime)
            assert parsed.data.items[0].change_date.tzinfo is not None

        if endpoint == "shareschg":
            assert parsed.data.items
            assert isinstance(parsed.data.items[0].change_date, datetime)
            assert parsed.data.items[0].change_date.tzinfo is not None
            if parsed.data.restrictions:
                assert isinstance(parsed.data.restrictions[0].release_time, datetime)
                assert parsed.data.restrictions[0].release_time.tzinfo is not None
