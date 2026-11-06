"""Numbered pages: the result type, the end rules and the state of one walk; nothing here sends."""

from __future__ import annotations

from collections.abc import Hashable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any, Generic, TypeVar

from ._result import Fetched
from .errors import InvalidParams, UpstreamChanged
from .registry import Endpoint, Pagination

T = TypeVar("T")
PageT = TypeVar("PageT", bound="Page[Any]")

RUNAWAY_PAGES = 1000
"""A walk with neither limit fails past this many pages rather than running for hours."""


@dataclass(frozen=True, slots=True)
class Page(Fetched, Generic[T]):
    """One upstream page; iterating it gives its rows.

    ``next_params`` holds the params of the next page, is safe to store as JSON and is the only
    way to resume a walk; None means the list ended there.
    """

    items: tuple[T, ...]
    page: int
    page_size: int
    """What the upstream echoes as the page size, or else what was requested."""
    total: int | None = None
    """The upstream's own count of rows, unchanged."""
    last_page: int | None = None
    capped: bool = False
    """The total reached the most rows the upstream serves, so some rows cannot be fetched."""
    short: bool = False
    """The list has no total and this page has fewer rows than ``page_size``: it may have ended,
    or the upstream's page size cap may be lower than declared."""
    next_params: Mapping[str, Any] | None = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


def finish(rule: Pagination, page: PageT, params: Mapping[str, Any]) -> PageT:
    """Set ``capped``, ``short`` and ``next_params``; raise ``ValueError`` when the page
    contradicts the upstream's total or lacks one it must have."""
    capped = bool(rule.total_cap and page.total is not None and page.total >= rule.total_cap)
    within_cap = rule.total_cap is None or page.page * page.page_size < rule.total_cap
    if rule.end == "total":
        if page.last_page is not None:
            more = page.page < page.last_page
        elif page.total is not None:
            more = page.page * page.page_size < page.total
        else:
            raise ValueError(f"page {page.page} has no total and no last page")
        more = more and within_cap
        if more and len(page) < page.page_size:
            raise ValueError(
                f"page {page.page} has {len(page)} rows of {page.page_size} but the upstream "
                f"counts {page.total} rows and {page.last_page} pages"
            )
    elif rule.end == "short":
        more = within_cap and len(page) >= page.page_size
    else:
        more = within_cap and len(page) > 0
    return replace(
        page,
        capped=capped,
        short=rule.end == "empty" and len(page) < page.page_size,
        next_params={**params, "page": page.page + 1} if more else None,
    )


class PageWalk:
    """State of one walk: the params of the next request, or None when it is over."""

    def __init__(
        self,
        name: str,
        rule: Pagination,
        params: Mapping[str, Any],
        *,
        max_pages: int | None,
        max_items: int | None,
    ) -> None:
        self.name = name
        self.rule = rule
        self.params: Mapping[str, Any] | None = params
        self.max_pages = max_pages
        self.max_items = max_items
        self.fetched = 0
        self.delivered = 0
        self.seen: set[Hashable] = set()
        self.previous: tuple[Any, ...] | None = None
        self.previous_short = False

    def accept(self, page: PageT) -> PageT:
        """The page to deliver, without rows already seen; raise when the walk cannot go on."""
        rule, sent = self.rule, self.params or {}
        self.fetched += 1
        if page.capped and self.max_items is None and self.max_pages is None:
            raise InvalidParams(
                f"{self.name} matches at least {rule.total_cap} rows, the most the upstream "
                "serves, so the rest would be lost",
                endpoint=self.name,
                params=sent,
                hint=f"{rule.cap_hint} Or pass max_items or max_pages to accept the first rows.",
            )
        rows = page.items
        if self.previous_short and rows and rule.guard_short:
            raise UpstreamChanged(
                f"{self.name} page {page.page - 1} came back short but page {page.page} has "
                "rows: the upstream ignored page_size",
                endpoint=self.name,
                params=sent,
            )
        if rule.key is not None:
            fresh = [row for row in rows if rule.key(row) not in self.seen]
            # Rows tied in the sort order can move between pages, so a page of known rows alone
            # proves nothing; the same page twice does.
            if rows and rows == self.previous:
                raise self._stuck(page, sent)
            self.seen.update(rule.key(row) for row in fresh)
            rows = tuple(fresh)
        elif rows and rows == self.previous:
            raise self._stuck(page, sent)
        self.previous = page.items
        self.previous_short = len(page) < page.page_size
        next_params = page.next_params
        if self.max_items is not None and self.delivered + len(rows) >= self.max_items:
            keep = self.max_items - self.delivered
            if keep < len(rows):
                next_params = sent  # the rows cut must come again: next_params never skips a row
            rows = rows[:keep]
            self.params = None
        elif self.max_pages is not None and self.fetched >= self.max_pages:
            self.params = None
        elif (
            self.max_pages is None
            and self.max_items is None
            and next_params is not None
            and self.fetched >= RUNAWAY_PAGES
        ):
            raise UpstreamChanged(
                f"{self.name} did not end within {RUNAWAY_PAGES} pages",
                endpoint=self.name,
                params=sent,
                hint="Pass max_pages to go further.",
            )
        else:
            self.params = next_params
        self.delivered += len(rows)
        return replace(page, items=rows, next_params=next_params)

    def _stuck(self, page: Page[Any], sent: Mapping[str, Any]) -> UpstreamChanged:
        return UpstreamChanged(
            f"{self.name} page {page.page} repeats rows already returned: the upstream ignored "
            "the page parameter",
            endpoint=self.name,
            params=sent,
        )


def start_walk(
    spec: Endpoint[Any, Any],
    params: Mapping[str, Any],
    max_pages: int | None,
    max_items: int | None,
) -> PageWalk:
    """Validate a walk before its first request; no ``page_size`` means the largest one."""
    rule = spec.pagination
    if rule is None or rule.style != "page":
        raise ValueError(f"{spec.name} has no numbered pages")
    if (max_pages is not None and max_pages < 1) or (max_items is not None and max_items < 1):
        raise InvalidParams("max_pages and max_items must be at least 1", endpoint=spec.name)
    merged = dict(params)
    if "page_size" in spec.params.model_fields and merged.get("page_size") is None:
        merged["page_size"] = rule.size_cap
    dump = spec.validate(merged).model_dump(mode="json", exclude_none=True)
    return PageWalk(spec.name, rule, dump, max_pages=max_pages, max_items=max_items)
