"""
Tushare HTTP 直连客户端（curl_cffi）
====================================
直连服务器对 requests/urllib 做 TLS 指纹拦截，必须用 curl_cffi impersonate=chrome。

Token 优先级：
  1. 环境变量 TUSHARE_TOKEN
  2. 同目录 .env 中的 TUSHARE_TOKEN
  3. 兼容旧路径 E:\\openclaw\\stock_picker_cn\\tushare_http.py 中的 TUSHARE_TOKEN 常量

用法：
    from tushare_http import pro
    df = pro.daily(trade_date="20260731")
"""
from __future__ import annotations

import os
import re
from functools import partial
from pathlib import Path

# 清除代理与 Hermes 注入的 PYTHONPATH，避免污染本机 Python 3.14
for _k in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "PYTHONPATH",
):
    os.environ.pop(_k, None)

import pandas as pd
from curl_cffi import requests as crequests

TUSHARE_HTTP_URL = os.environ.get("TUSHARE_HTTP_URL", "http://a.sszhixia.cn/").rstrip("/") + "/"


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _token_from_legacy() -> str:
    legacy = Path(r"E:\openclaw\stock_picker_cn\tushare_http.py")
    if not legacy.exists():
        return ""
    text = legacy.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'TUSHARE_TOKEN\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else ""


def _resolve_token() -> str:
    _load_dotenv()
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if token:
        return token
    token = _token_from_legacy()
    if token:
        return token
    raise RuntimeError(
        "缺少 TUSHARE_TOKEN。请在环境变量或 accumulation_breakout/.env 中设置，"
        "或保留 E:\\openclaw\\stock_picker_cn\\tushare_http.py 作为兼容回退。"
    )


class TushareHttpClient:
    """与 tushare pro.DataApi 兼容的 HTTP 直连客户端。"""

    def __init__(self, token: str | None = None, timeout: int = 30):
        self.__token = token or _resolve_token()
        self.__timeout = timeout
        self.__http_url = TUSHARE_HTTP_URL

    def query(self, api_name: str, fields: str = "", **kwargs) -> pd.DataFrame:
        req_params = {
            "api_name": api_name,
            "token": self.__token,
            "params": kwargs,
            "fields": fields,
        }
        res = crequests.post(
            f"{self.__http_url}{api_name}",
            json=req_params,
            impersonate="chrome",
            timeout=self.__timeout,
        )
        if not res:
            return pd.DataFrame()
        result = res.json()
        if result.get("code") != 0:
            raise RuntimeError(
                f"Tushare query failed: api={api_name}, error={result.get('msg')}"
            )
        data = result.get("data") or {}
        columns = data.get("fields") or []
        items = data.get("items") or []
        return pd.DataFrame(items, columns=columns)

    def __getattr__(self, name: str):
        return partial(self.query, name)

    @property
    def http_url(self) -> str:
        return self.__http_url


def get_pro() -> TushareHttpClient:
    return TushareHttpClient()


pro = get_pro()
setattr(pro, "_DataApi__http_url", TUSHARE_HTTP_URL)
setattr(pro, "_DataApi__token", getattr(pro, "_TushareHttpClient__token", ""))


if __name__ == "__main__":
    cal = pro.trade_cal(
        exchange="",
        start_date="20260701",
        end_date="20260802",
        fields="cal_date,is_open",
    )
    print("=== trade_cal ===")
    print(cal.head())
    basic = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,industry,list_date",
    )
    print(f"stock_basic rows: {len(basic)}")
