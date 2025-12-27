from __future__ import annotations

from datetime import date, timedelta

from xueqiu import XueqiuClient


def main() -> None:
    # CSIndex endpoints do not require Xueqiu auth (and are not hosted on xueqiu.com).
    with XueqiuClient(use_env=False) as client:
        index_code = "000300"  # CSI 300

        basic = client.csindex.index_basic_info(index_code)
        print("[csindex.index_basic_info]")
        print(basic.data)

        end = date.today()
        start = end - timedelta(days=30)
        perf = client.csindex.index_perf(index_code, start_date=start, end_date=end)
        print("[csindex.index_perf]")
        print(perf.data)


if __name__ == "__main__":
    main()
