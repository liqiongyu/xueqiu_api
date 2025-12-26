from __future__ import annotations

REALTIME_QUOTEC_PATH = "/v5/stock/realtime/quotec.json"
REALTIME_PANKOU_PATH = "/v5/stock/realtime/pankou.json"
REALTIME_QUOTE_DETAIL_PATH = "/v5/stock/quote.json"
KLINE_PATH = "/v5/stock/chart/kline.json"

# finance (stock.xueqiu.com)
FINANCE_CASH_FLOW_PATH = "/v5/stock/finance/cn/cash_flow.json"
FINANCE_INDICATOR_PATH = "/v5/stock/finance/cn/indicator.json"
FINANCE_BALANCE_PATH = "/v5/stock/finance/cn/balance.json"
FINANCE_INCOME_PATH = "/v5/stock/finance/cn/income.json"
FINANCE_BUSINESS_PATH = "/v5/stock/finance/cn/business.json"

# report (stock.xueqiu.com)
REPORT_LATEST_PATH = "/stock/report/latest.json"
REPORT_EARNING_FORECAST_PATH = "/stock/report/earningforecast.json"

# capital (stock.xueqiu.com)
CAPITAL_MARGIN_PATH = "/v5/stock/capital/margin.json"
CAPITAL_BLOCKTRANS_PATH = "/v5/stock/capital/blocktrans.json"
CAPITAL_ASSORT_PATH = "/v5/stock/capital/assort.json"
CAPITAL_FLOW_PATH = "/v5/stock/capital/flow.json"
CAPITAL_HISTORY_PATH = "/v5/stock/capital/history.json"

# f10 (stock.xueqiu.com)
F10_SKHOLDERCHG_PATH = "/v5/stock/f10/cn/skholderchg.json"
F10_SKHOLDER_PATH = "/v5/stock/f10/cn/skholder.json"
F10_INDUSTRY_PATH = "/v5/stock/f10/cn/industry.json"
F10_HOLDERS_PATH = "/v5/stock/f10/cn/holders.json"
F10_BONUS_PATH = "/v5/stock/f10/cn/bonus.json"
F10_ORG_HOLDING_CHANGE_PATH = "/v5/stock/f10/cn/org_holding/change.json"
F10_INDUSTRY_COMPARE_PATH = "/v5/stock/f10/cn/industry/compare.json"
F10_BUSINESS_ANALYSIS_PATH = "/v5/stock/f10/cn/business_analysis.json"
F10_SHARESCHG_PATH = "/v5/stock/f10/cn/shareschg.json"
F10_TOP_HOLDERS_PATH = "/v5/stock/f10/cn/top_holders.json"
F10_INDICATOR_PATH = "/v5/stock/f10/cn/indicator.json"

# portfolio (stock.xueqiu.com)
PORTFOLIO_LIST_PATH = "/v5/stock/portfolio/list.json"
PORTFOLIO_STOCK_LIST_PATH = "/v5/stock/portfolio/stock/list.json"

# xueqiu.com (main)
CUBE_NAV_DAILY_URL = "https://xueqiu.com/cubes/nav_daily/all.json"
CUBE_REBALANCING_HISTORY_URL = "https://xueqiu.com/cubes/rebalancing/history.json"
CUBE_REBALANCING_CURRENT_URL = "https://xueqiu.com/cubes/rebalancing/current.json"
CUBE_QUOTE_URL = "https://xueqiu.com/cubes/quote.json"

SUGGEST_STOCK_URL = "https://xueqiu.com/query/v1/suggest_stock.json"

# csindex (extra; does not require Xueqiu auth)
CSINDEX_INDEX_BASIC_INFO_URL = "https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info"
CSINDEX_INDEX_DETAILS_DATA_URL = "https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data"
CSINDEX_INDEX_WEIGHT_TOP10_URL = "https://www.csindex.com.cn/csindex-home/index/weight/top10"
CSINDEX_INDEX_PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
