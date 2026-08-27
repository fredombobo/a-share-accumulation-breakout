"""
Tushare 兼容层 —— 请改用 tushare_init
======================================
标准初始化见 `tushare_init.py`：

    import tushare as ts
    pro = ts.pro_api(TOKEN)
    pro._DataApi__http_url = 'https://a.sszhixia.cn'

新代码::

    from tushare_init import pro

旧代码仍可用::

    from tushare_http import pro
"""
from __future__ import annotations

from tushare_init import (  # noqa: F401
    DEFAULT_HTTP_URL,
    DEFAULT_TOKEN,
    TUSHARE_HTTP_URL,
    get_pro,
    init_pro,
    pro,
    resolve_http_url,
    resolve_token,
)

if __name__ == "__main__":
    p = get_pro()
    print("http_url =", p._DataApi__http_url)
    print(p.trade_cal(exchange="SSE", start_date="20260801", end_date="20260806"))
