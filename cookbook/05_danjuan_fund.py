from __future__ import annotations

from pprint import pprint

from xueqiu import XueqiuClient


def main() -> None:
    # Danjuan (蛋卷基金) endpoints do not require Xueqiu auth.
    with XueqiuClient(use_env=False) as client:
        fund_code = "008975"

        info = client.danjuan.fund_info(fund_code)
        print("[danjuan.fund_info]")
        pprint(info.data)

        nav = client.danjuan.fund_nav_history(fund_code, page=1, size=5)
        print("[danjuan.fund_nav_history]")
        pprint(nav.data)


if __name__ == "__main__":
    main()
