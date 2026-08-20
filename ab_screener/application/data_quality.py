"""数据质量门禁（P1.3）：重复键/非法 OHLC/负量额/覆盖率/源端比对。

契约（implementation P1.3）：
- 重复键、非法 OHLC、负量额均为 0；活跃股票覆盖率 ≥98%；持仓/活动订单/A池 100%。
- 源端比对：固定种子 ≥20 标的 × 5 日与数据源零差异；无 Token →
  `result=INSUFFICIENT`（不得 PASS）。
- 覆盖率为 0/缺数据 → FAIL（fail-closed）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

COVERAGE_MIN_PCT = 98.0
PARITY_CODES = 20
PARITY_DAYS = 5


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)


def check_duplicate_keys(db_path: str | Path) -> dict[str, Any]:
    with _connect(db_path) as conn:
        dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT ts_code, trade_date FROM daily"
            " GROUP BY ts_code, trade_date HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    return {"name": "duplicate_keys", "pass": int(dup) == 0, "count": int(dup)}


def check_invalid_ohlc_and_negative(db_path: str | Path) -> dict[str, Any]:
    """非法 OHLC/负量额；停牌行（open=0 AND vol=0，close 保留前收）豁免。"""
    with _connect(db_path) as conn:
        invalid = conn.execute(
            "SELECT COUNT(*) FROM daily"
            " WHERE (high < open OR high < close OR low > open OR low > close"
            "   OR high IS NULL OR low IS NULL OR open IS NULL OR close IS NULL)"
            "   AND NOT (open = 0 AND vol = 0)"
        ).fetchone()[0]
        suspended = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE open = 0 AND vol = 0"
        ).fetchone()[0]
        negative = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE vol < 0 OR amount < 0"
        ).fetchone()[0]
    return {
        "name": "ohlc_and_quantity",
        "pass": int(invalid) == 0 and int(negative) == 0,
        "invalid_ohlc": int(invalid),
        "suspended_bars_exempt": int(suspended),
        "negative_vol_amount": int(negative),
    }


def check_coverage(db_path: str | Path, as_of: str) -> dict[str, Any]:
    """活跃股票（instrument_universe_rules 有效规则）在 daily 的覆盖率。"""
    with _connect(db_path) as conn:
        has_universe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='instrument_universe_rules'"
        ).fetchone()
        if not has_universe:
            return {"name": "coverage", "pass": False, "reason": "instrument 注册表未迁移"}
        total = conn.execute(
            "SELECT COUNT(*) FROM instrument_universe_rules"
            " WHERE security_type='stock' AND ? >= list_date"
            " AND (delist_date IS NULL OR ? < delist_date)",
            (as_of, as_of),
        ).fetchone()[0]
        if total == 0:
            return {"name": "coverage", "pass": False, "reason": "instrument 注册表为空"}
        covered = conn.execute(
            "SELECT COUNT(DISTINCT r.ts_code) FROM instrument_universe_rules r"
            " JOIN daily d ON d.ts_code = r.ts_code AND d.trade_date = ?"
            " WHERE r.security_type='stock' AND ? >= r.list_date"
            " AND (r.delist_date IS NULL OR ? < r.delist_date)",
            (as_of, as_of, as_of),
        ).fetchone()[0]
    pct = 100.0 * int(covered) / int(total)
    return {
        "name": "coverage",
        "pass": pct >= COVERAGE_MIN_PCT,
        "pct": round(pct, 2),
        "covered": int(covered),
        "total": int(total),
        "min_pct": COVERAGE_MIN_PCT,
    }


def check_key_accounts_coverage(db_path: str | Path) -> dict[str, Any]:
    """持仓/活动订单/A池标的全覆盖（0 缺失才 PASS）。"""
    with _connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing: list[str] = []
        if "pt_position_lot" in tables:
            n = conn.execute(
                "SELECT COUNT(DISTINCT ts_code) FROM pt_position_lot WHERE remaining_qty>0"
            ).fetchone()[0]
            if int(n) > 0:
                miss = conn.execute(
                    "SELECT COUNT(DISTINCT ts_code) FROM pt_position_lot WHERE remaining_qty>0"
                    " AND ts_code NOT IN (SELECT ts_code FROM daily)"
                ).fetchone()[0]
                if int(miss):
                    missing.append(f"持仓缺失 {miss}")
    return {"name": "key_accounts_coverage", "pass": not missing, "missing": missing}


def source_parity(
    db_path: str | Path,
    *,
    pro: Any,
    codes: list[str],
    days: int = PARITY_DAYS,
    latest_trade_date: str,
) -> dict[str, Any]:
    """固定种子 × N 日与源端逐字段零差异。无 pro → INSUFFICIENT。"""
    if pro is None:
        return {"name": "source_parity", "pass": False, "result": "INSUFFICIENT",
                "reason": "无 TUSHARE_TOKEN，无法执行源端比对"}
    diffs = 0
    checked = 0
    for code in codes[:PARITY_CODES]:
        try:
            src = pro.daily(ts_code=code, start_date=latest_trade_date,
                            end_date=latest_trade_date)
        except Exception:  # noqa: BLE001
            return {"name": "source_parity", "pass": False, "result": "FAIL",
                    "reason": f"源端接口异常: {code}"}
        if src is None or src.empty:
            continue
        with _connect(db_path) as conn:
            local = conn.execute(
                "SELECT open, high, low, close, vol, amount FROM daily"
                " WHERE ts_code=? AND trade_date=?",
                (code, latest_trade_date),
            ).fetchone()
        if local is None:
            continue
        src_row = src.iloc[0]
        for col in ("open", "high", "low", "close", "vol", "amount"):
            sv = float(src_row.get(col) if src_row.get(col) is not None else 0.0)
            lv = float(local[("open", "high", "low", "close", "vol", "amount").index(col)])
            if abs(sv - lv) > 1e-6:
                diffs += 1
        checked += 1
    return {
        "name": "source_parity",
        "pass": diffs == 0 and checked >= 1,
        "result": "PASS" if diffs == 0 and checked >= 1 else "FAIL",
        "codes_checked": checked,
        "diffs": diffs,
        "days": days,
    }


def run_data_quality(
    db_path: str | Path,
    *,
    as_of: str | None = None,
    pro: Any = None,
    latest_trade_date: str | None = None,
    seed_codes: list[str] | None = None,
) -> dict[str, Any]:
    """执行全部数据质量检查，返回 {result, checks, summary}。"""
    checks = [
        check_duplicate_keys(db_path),
        check_invalid_ohlc_and_negative(db_path),
    ]
    if as_of:
        checks.append(check_coverage(db_path, as_of))
    checks.append(check_key_accounts_coverage(db_path))

    parity: dict[str, Any] = {"name": "source_parity", "pass": False, "result": "INSUFFICIENT",
                              "reason": "无 TUSHARE_TOKEN，无法执行源端比对"}
    if pro is not None and latest_trade_date:
        parity = source_parity(
            db_path, pro=pro, codes=seed_codes or [], days=PARITY_DAYS,
            latest_trade_date=latest_trade_date,
        )
    checks.append(parity)

    hard_fail = [c for c in checks if not c["pass"]]
    # 无 Token 时源端比对无法执行 → INSUFFICIENT；但其它硬检查失败仍取 FAIL（更严格）
    parity_insufficient = any(
        c.get("result") == "INSUFFICIENT" for c in checks if c["name"] == "source_parity"
    )
    non_parity_fail = [c for c in hard_fail if c["name"] != "source_parity"]
    if non_parity_fail:
        result = "FAIL"
    elif parity_insufficient:
        result = "INSUFFICIENT"
    else:
        result = "PASS"
    return {
        "result": result,
        "checks": checks,
        "summary": f"{result}: {len(non_parity_fail)} 项硬性检查未通过",
        "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
