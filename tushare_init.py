"""
Tushare 唯一标准初始化入口（全项目只引用本文件）
================================================

用户指定的标准调用方式：

```python
import os
import tushare as ts
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
pro._DataApi__http_url = 'http://a.sszhixia.cn/'
```

其余模块统一：

```python
from tushare_init import pro
# 或
from tushare_init import get_pro
pro = get_pro()
```

兼容旧写法：`from tushare_http import pro`（转发到本模块）。

说明：
  - 直连节点 `a.sszhixia.cn` 会对裸 `requests` TLS 指纹拦截（10054），
    本文件在官方 `ts.pro_api` 初始化后，用 curl_cffi 接管 query，调用方式不变。
  - Token 必须通过项目 `.env` 或环境变量的 `TUSHARE_TOKEN` 提供；项目
    `.env` 是本项目权威配置，避免父进程残留旧 Token。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# 清除代理污染
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
import tushare as ts
from curl_cffi import requests as crequests
from tushare.pro.client import DataApi

# ═══════════════════════════════════════════════════════════
#  标准常量（与用户指定调用方式一一对应）
# ═══════════════════════════════════════════════════════════
# pro = ts.pro_api('<此 token>')
DEFAULT_TOKEN = ""
# pro._DataApi__http_url = 'http://a.sszhixia.cn/'
DEFAULT_HTTP_URL = "http://a.sszhixia.cn/"

_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_dotenv() -> None:
    """加载本项目 Tushare 配置；项目文件覆盖父进程中的陈旧值。"""
    if not _ENV_PATH.exists():
        return
    allowed = {"TUSHARE_TOKEN", "TUSHARE_HTTP_URL"}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key in allowed:
            os.environ[key] = val


def _resolve_token() -> str:
    _load_dotenv()
    return (os.environ.get("TUSHARE_TOKEN") or DEFAULT_TOKEN).strip()


def _resolve_http_url() -> str:
    _load_dotenv()
    raw = (os.environ.get("TUSHARE_HTTP_URL") or DEFAULT_HTTP_URL).strip()
    return raw.rstrip("/") + "/"


def sanitize_error(error: object) -> str:
    """清除异常文本中的凭据，供所有日志边界统一调用。"""
    message = str(error)
    token = _resolve_token()
    if token:
        message = message.replace(token, "[REDACTED]")
    message = re.sub(
        r"(?i)(token\s*(?:不对|错误|invalid|[:=])?\s*[，,:：]?\s*)([A-Za-z0-9_-]{20,})",
        r"\1[REDACTED]",
        message,
    )
    return message


def _patch_dataapi_query_with_curl_cffi() -> None:
    """官方 DataApi 用 requests.post，直连会 10054；改为 curl_cffi。"""
    if getattr(DataApi, "_ab_curl_patched", False):
        return

    def query(self: DataApi, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        token = object.__getattribute__(self, "_DataApi__token")
        http_url = object.__getattribute__(self, "_DataApi__http_url")
        timeout = object.__getattribute__(self, "_DataApi__timeout")
        req_params = {
            "api_name": api_name,
            "token": token,
            "params": kwargs,
            "fields": fields,
        }
        # 与官方一致: f"{url}/{api_name}"
        url = f"{str(http_url).rstrip('/')}/{api_name}"
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                res = crequests.post(
                    url,
                    json=req_params,
                    impersonate="chrome",
                    timeout=timeout,
                )
                status_code = int(getattr(res, "status_code", 0) or 0)
                if status_code >= 400:
                    raise RuntimeError(f"数据网关 HTTP {status_code}")
                try:
                    result = json.loads(res.text)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise RuntimeError(
                        f"数据网关返回非 JSON 响应（HTTP {status_code or 'unknown'}）"
                    ) from exc
                if result.get("code") != 0:
                    message = result.get("msg") or f"Tushare error code={result.get('code')}"
                    raise RuntimeError(sanitize_error(message))
                data = result.get("data") or {}
                columns = data.get("fields") or []
                items = data.get("items") or []
                return pd.DataFrame(items, columns=columns)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(
            f"数据网关连续 3 次请求失败: {sanitize_error(last_err)}"
        ) from last_err

    DataApi.query = query
    DataApi._ab_curl_patched = True


def init_pro(token: str | None = None, http_url: str | None = None, timeout: int = 30):
    """按用户指定方式初始化（唯一实现点）。

    等价于::

        pro = ts.pro_api(token)
        pro._DataApi__http_url = 'http://a.sszhixia.cn/'
    """
    _patch_dataapi_query_with_curl_cffi()
    tok = (token or _resolve_token()).strip()
    url = (http_url or _resolve_http_url()).strip().rstrip("/") + "/"

    # ── 标准两行（勿在其他文件重复）──
    pro = ts.pro_api(tok, timeout=timeout)
    pro._DataApi__http_url = url
    return pro


_pro = None


def get_pro(force_refresh: bool = False):
    """获取全局 pro 单例。"""
    global _pro
    if _pro is None or force_refresh:
        _pro = init_pro()
    return _pro


def __getattr__(name: str):
    if name == "pro":
        return get_pro()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 模块加载时初始化（标准入口）
pro = init_pro()
TUSHARE_HTTP_URL = getattr(pro, "_DataApi__http_url", DEFAULT_HTTP_URL)

# 对外别名
resolve_token = _resolve_token
resolve_http_url = _resolve_http_url


if __name__ == "__main__":
    print("http_url =", pro._DataApi__http_url)
    cal = pro.trade_cal(
        exchange="SSE",
        start_date="20260801",
        end_date="20260806",
        fields="cal_date,is_open",
    )
    print("=== trade_cal ===")
    print(cal)
    basic = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,industry,list_date",
    )
    print(f"stock_basic rows: {len(basic)}")
