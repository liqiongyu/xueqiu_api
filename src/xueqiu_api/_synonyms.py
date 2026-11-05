"""Query-side synonyms of the endpoint search: canonical word to its aliases.

An alias in a query adds the terms of its canonical word at a lower weight; the index holds none
of this table. A word of a single endpoint belongs in that endpoint's keywords. An alias earns its
place here by reaching an endpoint its canonical word reaches and its own terms do not.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

SYNONYMS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "前复权": ("qfq", "forward adjusted", "forward-adjusted"),
        "后复权": ("hfq", "backward adjusted", "backward-adjusted"),
        "实时行情": ("股价", "现价", "多少钱"),
        "盘口": ("买一价", "卖一价", "买一卖一", "level 2"),
        "分时": ("timeline",),
        "K线": (
            "蜡烛图",
            "走势图",
            "历史价格",
            "区间涨跌",
            "今年以来",
            "年初至今",
            "candle",
            "ohlc",
            "historical prices",
        ),
        "融资融券": ("rzrq",),
        "龙虎榜": ("交易公开信息", "lhb"),
        "大宗交易": ("block trade",),
        "机构持股": ("institution",),
        "估值": ("贵不贵",),
        "市盈率": ("pe", "p/e", "ttm"),
        "市净率": ("pb", "p/b"),
        "百分位": ("percentile",),
        "排行": ("排名", "榜单", "ranking", "leaderboard"),
        "涨幅榜": ("涨得最多",),
        "行业": ("板块", "sector"),
        "净值": ("nav", "net asset value"),
        "基金费率": ("手续费",),
        "基金排行": ("收益最高",),
        "组合调仓记录": ("rebalance",),
    }
)
