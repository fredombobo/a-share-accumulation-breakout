"""龙虎榜真实网关 smoke：只经 tushare_init，无 Token 则退出。

用法（项目根）:
  .venv312\\Scripts\\python.exe scripts\\lhb_tushare_smoke.py

输出 runtime/lhb_smoke_last.json（不含 Token / 不含完整原始行）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tushare_init import get_pro, resolve_http_url, resolve_token, sanitize_error

_TZ = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "runtime" / "lhb_smoke_last.json"


def _fields(df) -> list[str]:
    if df is None or getattr(df, "empty", True):
        return []
    return [str(c) for c in list(df.columns)]


def main() -> int:
    if not resolve_token():
        print("SKIP: TUSHARE_TOKEN 未配置，不访问网络")
        return 2
    pro = get_pro()
    url = resolve_http_url()
    report: dict[str, object] = {
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "http_url": url,
        "token_present": True,
        "token_preview": "REDACTED",
        "apis": {},
    }
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date="20260801",
            end_date="20260829",
            fields="cal_date,is_open",
        )
        open_days = sorted(
            str(r["cal_date"])
            for r in cal.to_dict("records")
            if int(r.get("is_open") or 0) == 1
        )
        trade_date = open_days[-1] if open_days else "20260828"
        report["trade_date"] = trade_date
        for name, getter in (
            ("top_list", lambda: pro.top_list(trade_date=trade_date)),
            ("top_inst", lambda: pro.top_inst(trade_date=trade_date)),
            ("hm_list", lambda: pro.hm_list()),
        ):
            try:
                df = getter()
                rows = 0 if df is None or getattr(df, "empty", True) else int(len(df))
                report["apis"][name] = {  # type: ignore[index]
                    "ok": True,
                    "rows": rows,
                    "fields": _fields(df),
                }
            except Exception as exc:  # noqa: BLE001
                report["apis"][name] = {  # type: ignore[index]
                    "ok": False,
                    "error": sanitize_error(exc),
                }
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", sanitize_error(exc))
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
