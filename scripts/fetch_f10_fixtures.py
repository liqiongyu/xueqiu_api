from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xueqiu import XueqiuClient
from xueqiu.api.urls import (
    F10_BONUS_PATH,
    F10_BUSINESS_ANALYSIS_PATH,
    F10_HOLDERS_PATH,
    F10_INDICATOR_PATH,
    F10_INDUSTRY_COMPARE_PATH,
    F10_INDUSTRY_PATH,
    F10_ORG_HOLDING_CHANGE_PATH,
    F10_SHARESCHG_PATH,
    F10_SKHOLDER_PATH,
    F10_SKHOLDERCHG_PATH,
    F10_TOP_HOLDERS_PATH,
)

DEFAULT_SYMBOLS = ["SZ002437", "SH600887", "SH601318"]
DEFAULT_ENDPOINTS = [
    "industry",
    "business_analysis",
    "skholder",
    "skholderchg",
    "shareschg",
]


def _get_cookie() -> str | None:
    return os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE")


def _redact(obj: Any) -> Any:
    # Defensive redaction: these endpoints shouldn't return auth/user fields,
    # but keep this anyway for safety.
    sensitive_keys = {
        "cookie",
        "cookies",
        "xq_a_token",
        "xq_r_token",
        "token",
        "access_token",
        "refresh_token",
        "uid",
        "user_id",
        "phone",
        "mobile",
        "email",
    }

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key in sensitive_keys:
                out[key] = "<redacted>"
                continue
            out[key] = _redact(value)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch real Xueqiu F10 payloads into tests/fixtures."
    )
    parser.add_argument(
        "--out-dir",
        default="tests/fixtures/f10",
        help="Directory to write JSON fixtures into (default: tests/fixtures/f10).",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols (default: SZ002437,SH600887,SH601318).",
    )
    parser.add_argument(
        "--endpoints",
        default=",".join(DEFAULT_ENDPOINTS),
        help=(
            "Comma-separated endpoints to fetch. Available: "
            "industry,business_analysis,skholder,skholderchg,shareschg,"
            "holders,org_holding_change,bonus,indicator,industry_compare,top_holders"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing fixture files.",
    )
    args = parser.parse_args()

    cookie = _get_cookie()
    if not cookie:
        raise SystemExit(
            "Missing auth. Set `XUEQIU_TOKEN` (recommended) or `XUEQIU_COOKIE`.\n"
            "Example:\n"
            "  export XUEQIU_TOKEN='xq_a_token=...; u=...'\n"
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols = [s.strip() for s in str(args.symbols).split(",") if s.strip()]
    if not symbols:
        raise SystemExit("No symbols provided.")

    def get(path: str, symbol: str, *, params: dict[str, Any] | None = None) -> Any:
        q = {"symbol": symbol}
        if params:
            q.update(params)
        return client.request_json("GET", path, params=q, require_auth=True)

    all_endpoints: dict[str, Callable[[str], Any]] = {
        "industry": lambda symbol: get(F10_INDUSTRY_PATH, symbol),
        "business_analysis": lambda symbol: get(F10_BUSINESS_ANALYSIS_PATH, symbol),
        "skholder": lambda symbol: get(F10_SKHOLDER_PATH, symbol),
        "skholderchg": lambda symbol: get(F10_SKHOLDERCHG_PATH, symbol),
        "shareschg": lambda symbol: get(F10_SHARESCHG_PATH, symbol, params={"count": 20}),
        "holders": lambda symbol: get(F10_HOLDERS_PATH, symbol),
        "org_holding_change": lambda symbol: get(F10_ORG_HOLDING_CHANGE_PATH, symbol),
        "bonus": lambda symbol: get(F10_BONUS_PATH, symbol, params={"page": 1, "size": 10}),
        "indicator": lambda symbol: get(F10_INDICATOR_PATH, symbol),
        "industry_compare": lambda symbol: get(
            F10_INDUSTRY_COMPARE_PATH, symbol, params={"type": "single"}
        ),
        "top_holders": lambda symbol: get(F10_TOP_HOLDERS_PATH, symbol, params={"circula": 1}),
    }
    selected = [e.strip() for e in str(args.endpoints).split(",") if e.strip()]
    if not selected:
        raise SystemExit("No endpoints selected.")

    unknown = sorted(set(selected) - set(all_endpoints))
    if unknown:
        raise SystemExit(f"Unknown endpoints: {', '.join(unknown)}")

    endpoints = {name: all_endpoints[name] for name in selected}

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "endpoints": sorted(endpoints.keys()),
    }
    _write_json(out_dir / "_meta.json", meta)

    with XueqiuClient(cookie=cookie) as client:
        for symbol in symbols:
            for name, fn in endpoints.items():
                path = out_dir / f"{symbol}__{name}.json"
                if path.exists() and not args.overwrite:
                    print(f"[skip] {path} exists (use --overwrite)")
                    continue
                print(f"[fetch] {symbol} {name}")
                payload = fn(symbol)
                _write_json(path, _redact(payload))
                print(f"[write] {path}")


if __name__ == "__main__":
    main()
