"""
Alias package for environments where `import xueqiu` may conflict.

PyPI name: `xueqiu_api`
Primary module: `xueqiu`
Fallback module: `xueqiu_api`
"""

from __future__ import annotations

from xueqiu import *  # noqa: F403
