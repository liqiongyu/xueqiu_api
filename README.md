# xueqiu-api

雪球和蛋卷基金数据的 Python 客户端，包括同步和异步 SDK、本地数据仓库、CLI 和 MCP server。项目处于早期开发阶段，路线图见 [PLAN.md](PLAN.md)。

A Python client for Xueqiu (雪球) and Danjuan market data, with a sync and async SDK, a local data store, a CLI and an MCP server. Early development; see [PLAN.md](PLAN.md) for the roadmap.

## 安装 / Install

还没有发布到 PyPI，先从 GitHub 安装；不用 MCP server 时去掉 `[mcp]`。Not on PyPI yet, so install from GitHub; drop `[mcp]` if you do not need the MCP server.

```bash
pip install "xueqiu-api[mcp] @ git+https://github.com/liqiongyu/xueqiu_api"
```

## 用法 / Usage

```python
from xueqiu_api import Client

with Client() as client:
    for quote in client.batch_quote(["SH600000", "00700", "AAPL"]):
        print(quote.symbol, quote.name, quote.current, quote.percent)

    bars = client.kline("SH600000", count=3)
    print(bars.timestamps[-1], bars.columns["close"][-1])

    income = client.finance_income("SH600000", count=2)
    latest = income.periods[0]
    print(latest.report_name, latest.items["total_revenue"].value)
```

`AsyncClient` 有同样的方法。目前有 72 个端点，覆盖行情、K 线、资金流向、财报、F10、选股器和榜单、指数估值、基金和雪球组合。分页的列表另有 `<name>_pages` 方法逐页取数，`kline_history` 取一段完整的 K 线。`security_snapshot`、`trading_calendar`、`market_state` 和 `adjust_factors` 给出证券列表、交易日历、市场状态和复权因子，`xueqiu_api.quality` 校验数据。其中 68 个端点不需要登录。组合持仓、组合调仓、自选股分组和自选股这 4 个要把登录 cookie 放进环境变量 `XUEQIU_TOKEN`，它们的返回结构还没有用真实账号验证。

`AsyncClient` has the same methods. There are 72 endpoints so far, covering quotes, kline, capital flow, financial statements, F10, the screener and rankings, index valuation, funds and Xueqiu cubes. Paged lists also have a `<name>_pages` method, and `kline_history` returns a whole date range. `security_snapshot`, `trading_calendar`, `market_state` and `adjust_factors` give the security list, trading calendar, market state and adjustment factors, and `xueqiu_api.quality` checks the data. 68 endpoints work without login. Cube holdings, cube rebalancing, watchlist groups and watchlist stocks need a login cookie string in the `XUEQIU_TOKEN` environment variable, and their response shapes have not been checked against a real account.

```bash
xueqiu doctor   # 逐个调用端点并报告状态 / call every endpoint once and report
xueqiu mcp      # 在 stdio 上运行 MCP server / run the MCP server on stdio
```

MCP server 提供 5 个只读工具：`xq_search`、`xq_quote`、`xq_kline`、`xq_endpoints` 和 `xq_call`。客户端配置示例：The MCP server has five read-only tools: `xq_search`, `xq_quote`, `xq_kline`, `xq_endpoints` and `xq_call`. A client configuration example:

```json
{
  "mcpServers": {
    "xueqiu": {
      "command": "uvx",
      "args": ["--from", "xueqiu-api[mcp] @ git+https://github.com/liqiongyu/xueqiu_api", "xueqiu", "mcp"]
    }
  }
}
```

## 免责声明

本项目是非官方的第三方开源工具，与雪球（北京雪球信息科技有限公司）、蛋卷基金及其关联公司没有任何关联，也没有获得它们的授权或认可。

本项目通过雪球和蛋卷网页、App 背后未公开的接口获取数据。这些接口没有兼容承诺，可能随时变化、限流或停止服务。

《雪球服务协议》对以程序方式访问、抓取和存储其服务内容，以及把平台内容提供给 AI 或用于检索增强生成，都有限制性条款。使用前请自行阅读并遵守雪球和蛋卷基金的用户协议，判断你的用途是否需要事先取得授权，并自行承担相应责任。

本项目面向个人学习和研究。通过它获取的数据，权利归原平台和原始权利人，不在 MIT 许可范围内。请勿高频抓取，也不要用它采集社区用户发布的内容。客户端默认限速，请勿绕开。

数据可能延迟、缺失或有误，不构成投资建议。

本项目按 MIT 许可证提供，不提供任何担保，作者不对使用本项目造成的损失或纠纷负责。

如果你是相关权利人，认为本项目损害了你的合法权益，请通过 GitHub issue 联系维护者。

## Disclaimer

This is an unofficial third-party open-source project. It is not affiliated with, authorized by or endorsed by Xueqiu (雪球, operated by 北京雪球信息科技有限公司), Danjuan Funds or their affiliates.

It retrieves data through undocumented endpoints behind the Xueqiu and Danjuan websites and apps. These endpoints carry no compatibility promise and may change, be rate-limited or stop working at any time.

The Xueqiu service agreement restricts programmatic access to, scraping of and storage of its services, and restricts providing platform content to AI systems or using it for retrieval-augmented generation. Read and follow the Xueqiu and Danjuan terms yourself, decide whether your use needs prior permission, and take responsibility for that use.

This project is intended for personal study and research. Rights in the data it retrieves belong to the platforms and the original rights holders and are not covered by the MIT License. Do not use it for high-frequency scraping or to collect content posted by community users. The client is rate-limited by default; do not bypass the limits.

Data may be delayed, incomplete or wrong. Nothing here is investment advice.

Provided under the MIT License, without warranty of any kind. The authors are not liable for any loss or dispute arising from its use.

If you hold rights in the data or services involved and believe this project harms your legitimate interests, contact the maintainer through a GitHub issue.

## 许可证

[MIT](LICENSE)
