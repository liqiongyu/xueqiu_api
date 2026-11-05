"""The local endpoint search: its terms, its ranking over the registry and the synonym table."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import unicodedata
from dataclasses import replace
from typing import Any

import pytest

from xueqiu_api import REGISTRY
from xueqiu_api._search import EndpointIndex, terms
from xueqiu_api._synonyms import SYNONYMS
from xueqiu_api.registry import Endpoint

from .harness import synthetic_endpoint

OWN_KEYWORD_TOP3 = (582, 602)
"""Own keywords that rank their endpoint in the top 3: first measured 2026-11-05 on 72 endpoints.
A floor on the share of them; raise it when the catalog text improves it."""

INDEX = EndpointIndex(REGISTRY.values())
ALIASES = [(canonical, alias) for canonical, group in SYNONYMS.items() for alias in group]


def names(query: str, index: EndpointIndex = INDEX) -> list[str]:
    return [hit.name for hit in index.search(query)]


def document(name: str, *keywords: str, summary: str = "") -> Endpoint[Any, Any]:
    """An endpoint whose name and titles match no query of these tests."""
    agent = synthetic_endpoint().agent
    agent = replace(
        agent, title_zh=f"标题{name}", title_en=name, summary=summary, keywords=keywords
    )
    return synthetic_endpoint(name, agent=agent)


def document_terms(spec: Endpoint[Any, Any]) -> set[str]:
    agent = spec.agent
    texts = (spec.name, agent.title_zh, agent.title_en, agent.summary, *agent.keywords)
    return {term for text in texts for term in terms(text)}


DOCUMENTS = {name: document_terms(spec) for name, spec in REGISTRY.items()}


def reached_by_canonical(canonical: str) -> set[str]:
    return {name for name, found in DOCUMENTS.items() if found.issuperset(terms(canonical))}


# Terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("K线", ["k", "k线"]),
        ("A股", ["a", "a股"]),
        ("北证50", ["北证", "证50", "50"]),
        ("\uff30\uff25", ["pe"]),  # full-width PE
        ("top holders", ["top", "holder"]),
        ("融资融券", ["融资", "资融", "融券"]),
        ("f10_top_holders", ["f10", "top", "holder"]),
        ("class bus", ["class", "bus"]),
        ("股", []),
        ("abcd股", ["abcd"]),
        ("，、 ()", []),
    ],
)
def test_terms_of_a_text(text: str, expected: list[str]) -> None:
    assert terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A股", ["a股"]),
        ("雪球上的分红", ["球上", "上的", "的分", "分红"]),
        ("what is this dividend", ["dividend"]),
        ("Xueqiu SNOWBALL danjuan 蛋卷", []),
    ],
)
def test_a_query_loses_its_stop_words(text: str, expected: list[str]) -> None:
    assert terms(text, query=True) == expected


# Ranking over the registry


@pytest.mark.parametrize("name", REGISTRY)
def test_an_endpoint_ranks_first_by_its_name_and_both_titles(name: str) -> None:
    agent = REGISTRY[name].agent
    assert [names(text)[0] for text in (name, agent.title_zh, agent.title_en)] == [name] * 3


def test_an_exact_match_ignores_case_and_full_width_forms() -> None:
    assert names("\uff2b\uff2c\uff29\uff2e\uff25")[0] == "kline"  # full-width KLINE
    assert names(" Candlestick Bars ")[0] == "kline"


def test_own_keywords_keep_ranking_their_endpoint_in_the_top_3() -> None:
    found = [
        name in names(keyword)[:3]
        for name, spec in REGISTRY.items()
        for keyword in spec.agent.keywords
    ]
    hits, total = OWN_KEYWORD_TOP3
    assert sum(found) / len(found) >= hits / total


def test_the_profit_word_finds_the_income_statement_first() -> None:
    assert names("利润")[0] == "finance_income"


@pytest.mark.parametrize("query", ["", "   ", "，。", "雪球", "the", "no such data", "股"])
def test_a_query_without_a_matching_term_finds_nothing(query: str) -> None:
    assert INDEX.search(query) == []


def test_an_endpoint_left_out_of_the_index_is_not_found() -> None:
    index = EndpointIndex(spec for name, spec in REGISTRY.items() if name != "capital_margin")
    agent = REGISTRY["capital_margin"].agent
    for query in ("capital_margin", agent.title_zh, agent.title_en, *agent.keywords):
        assert "capital_margin" not in names(query, index), query


def test_an_empty_index_finds_nothing() -> None:
    assert EndpointIndex(()).search("融资融券") == []


# Scoring rules


def test_a_score_is_bm25_over_weighted_fields() -> None:
    specs = [document("aaa", "widget", "filler words"), document("bbb", summary="widget")]
    index = EndpointIndex(specs, synonyms={})

    def bm25(idf: float, frequency: float, length: int) -> float:
        # The two documents have 8 and 6 terms, so 7 on average.
        saturation = 1.2 * (0.75 + 0.25 * length / 7)
        return idf * frequency * 2.2 / (frequency + saturation)

    widget = {hit.name: hit.score for hit in index.search("widget")}
    assert widget["aaa"] == pytest.approx(bm25(math.log(1.2), 4, 8), abs=1e-6)  # keyword 4
    assert widget["bbb"] == pytest.approx(bm25(math.log(1.2), 1, 6), abs=1e-6)  # summary 1
    # In both titles at 5 and in the name at 3.
    [own] = index.search("aaa")
    assert own.score == pytest.approx(bm25(math.log(2), 13, 8), abs=1e-6)


def test_an_exact_match_beats_a_higher_score_and_sets_no_floor() -> None:
    index = EndpointIndex(
        [
            synthetic_endpoint("alpha_exact"),
            document("rich", *["alpha_exact"] * 9, summary="alpha exact"),
            document("thin", summary="alpha"),
        ],
        synonyms={},
    )

    hits = index.search("alpha_exact")
    assert [hit.name for hit in hits][:2] == ["alpha_exact", "rich"]
    assert hits[0].score < hits[1].score


def test_scores_under_a_fifth_of_the_best_are_dropped() -> None:
    others = [f"other{i}" for i in range(5)]
    specs = [document("best", "rare", "common"), *(document(name, "common") for name in others)]
    index = EndpointIndex(specs, synonyms={})
    hits = {hit.name: hit.score for hit in index.search("common")}

    assert hits["best"] * 0.2 < hits["other0"]
    assert names("rare common", index) == ["best"]
    assert [hit.name for hit in index.search("rare common", only=others)] == others


def test_a_search_returns_at_most_the_cap_with_ties_ordered_by_name() -> None:
    specs = [document(f"same{i:02d}", "widget") for i in reversed(range(13))]

    found = names("widget", EndpointIndex(specs, synonyms={}))

    assert found == [f"same{i:02d}" for i in range(10)]


def test_synonyms_expand_in_the_query_only_and_at_a_lower_weight() -> None:
    specs = [document("canonical", "前复权"), document("alias", "qfq")]
    index = EndpointIndex(specs, synonyms={"前复权": ("qfq",)})

    assert names("前复权", index) == ["canonical"]
    assert set(names("qfq", index)) == {"canonical", "alias"}
    direct = index.search("前复权")[0].score
    expanded = {hit.name: hit.score for hit in index.search("qfq")}["canonical"]
    assert expanded == pytest.approx(0.7 * direct, abs=1e-5)


def test_a_term_in_the_query_keeps_its_full_weight_over_an_alias() -> None:
    index = EndpointIndex([document("canonical", "前复权")], synonyms={"前复权": ("qfq",)})

    assert index.search("前复权 qfq")[0].score == index.search("前复权")[0].score


def test_an_ascii_alias_needs_word_boundaries_and_a_cjk_one_does_not() -> None:
    assert names("open") == []
    assert "quote_detail" in names("pe")
    assert "f10_indicator" in names("PE")
    assert names("多少钱")[0] in ("batch_quote", "quotec")
    assert set(names("这个股票多少钱了")) >= {"batch_quote", "quotec"}


def test_a_category_filter_applies_before_the_floor_and_the_cap() -> None:
    assert "f10_executive_trades" not in names("block trade")
    found = INDEX.search("block trade", only=("f10_executive_trades", "kline"))
    assert [hit.name for hit in found] == ["f10_executive_trades"]


def test_ranks_do_not_depend_on_the_hash_seed() -> None:
    code = (
        "import json\n"
        "from xueqiu_api import REGISTRY\n"
        "from xueqiu_api._search import EndpointIndex\n"
        "index = EndpointIndex(REGISTRY.values())\n"
        "agents = [spec.agent for spec in REGISTRY.values()]\n"
        "queries = [text for a in agents for text in (a.summary, *a.keywords)]\n"
        "ranks = [[(h.name, h.score) for h in index.search(q)] for q in queries]\n"
        "print(json.dumps({'hash': hash('xueqiu'), 'ranks': ranks}))\n"
    )

    runs = [
        json.loads(
            subprocess.run(
                [sys.executable, "-c", code],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        for seed in ("1", "2")
    ]

    assert runs[0]["hash"] != runs[1]["hash"]
    assert runs[0]["ranks"] == runs[1]["ranks"]


# The synonym table


@pytest.mark.parametrize("canonical", SYNONYMS)
def test_some_endpoint_holds_all_terms_of_a_canonical_word(canonical: str) -> None:
    assert reached_by_canonical(canonical)


def test_aliases_are_normalized_unique_and_no_canonical_word() -> None:
    aliases = [alias for _, alias in ALIASES]
    canonical = {unicodedata.normalize("NFKC", word).casefold() for word in SYNONYMS}
    assert len(aliases) == len(set(aliases))
    assert not canonical & set(aliases)
    assert all(alias == unicodedata.normalize("NFKC", alias).casefold() for alias in aliases)


def test_the_adjust_values_are_no_aliases() -> None:
    """``before`` is both an adjust value and the name of a paging parameter."""
    assert not {"before", "after", "normal"} & {alias for _, alias in ALIASES}


@pytest.mark.parametrize(("canonical", "alias"), ALIASES)
def test_an_alias_reaches_an_endpoint_its_own_terms_do_not(canonical: str, alias: str) -> None:
    own = terms(alias, query=True)
    assert any(DOCUMENTS[name].isdisjoint(own) for name in reached_by_canonical(canonical))
