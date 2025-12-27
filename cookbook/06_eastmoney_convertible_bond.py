from __future__ import annotations

from pprint import pprint

from xueqiu import XueqiuClient


def main() -> None:
    # Eastmoney datacenter endpoints do not require Xueqiu auth.
    with XueqiuClient(use_env=False) as client:
        resp = client.eastmoney.convertible_bond(20, 1)

        print("[eastmoney.convertible_bond]")
        pprint(resp.result)


if __name__ == "__main__":
    main()
