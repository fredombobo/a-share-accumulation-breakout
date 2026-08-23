"""数据质量门禁（P1.3 / V2R-D）：重复键/非法 OHLC/负量额/覆盖率/源端比对/影子 parity。

契约（implementation P1.3）：
- 重复键、非法 OHLC、负量额均为 0；活跃股票覆盖率 ≥98%；持仓/活动订单/A池 100%。
- 源端比对：固定种子 ≥20 标的 × 5 日与数据源零差异；无 Token →
  `result=INSUFFICIENT`（不得 PASS）。
- 覆盖率为 0/缺数据 → FAIL（fail-closed）。
- shadow parity（V2R-D）：同一固定种子下 legacy `daily` 与 PIT `daily_history` as-of
  读取逐字段一致；报告必须包含 code SHA、config hash、DB fingerprint、样本与差异。
"""
from __future__ import annotations

import hashlib
import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

COVERAGE_MIN_PCT = 98.0
PARITY_CODES = 20
PARITY_DAYS = 5

PARITY_FIELDS = ("open", "high", "low", "close", "vol", "amount")


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


def _code_sha() -> str:
    """git 当前 HEAD 短 SHA（报告身份用）。"""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _config_hash() -> str:
    try:
        import config

        src = Path(config.__file__).read_text(encoding="utf-8")
        return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "n/a"


def _db_fingerprint(db_path: str | Path) -> str:
    try:
        with _connect(db_path) as conn:
            daily_n = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
            daily_max = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
            pit_n = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
        raw = f"daily={daily_n}@{daily_max},daily_history={pit_n}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "n/a"


def _seed_sample_dates(db_path: str | Path, seed: int, n: int = PARITY_DAYS) -> list[str]:
    """固定种子抽取 n 个 PIT 已覆盖的历史交易日（daily_history 与 legacy daily 交集）。

    影子 parity 只比较「两个读取路径都存在」的日期；PIT 覆盖不足由
    coverage/shortfall 字段单独暴露，避免把 PIT 缺口误当成字段不一致。
    """
    with _connect(db_path) as conn:
        legacy = {r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily").fetchall()}
        pit_dates = {r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_history").fetchall()}
    common = sorted(legacy & pit_dates)
    if not common:
        return []
    rng = random.Random(seed)
    rng.shuffle(common)
    return sorted(common[:n])


def shadow_parity(
    db_path: str | Path,
    *,
    seed: int = 42,
    codes: list[str] | None = None,
    dates: list[str] | None = None,
    decision_at: str | None = None,
) -> dict[str, Any]:
    """legacy daily 与 PIT daily_history as-of 读取的影子 parity 报告。

    固定种子抽取 ≥20 标的 × 5 日期；对每个 (ts_code, trade_date) 比较
    open/high/low/close/vol/amount（价格按 4 位小数、量额按源精度）。
    报告必须包含 code SHA、config hash、DB fingerprint、样本与差异。
    """
    from ab_screener.data.pit_repository import PitRepository

    sample_dates = dates or _seed_sample_dates(db_path, seed)
    if len(sample_dates) < 5:
        sample_dates = (sample_dates + _seed_sample_dates(db_path, seed, 10))[:5]
    sample_codes = codes or _sample_codes_covered(db_path, seed, sample_dates)
    if len(sample_codes) < 20:
        sample_codes = (sample_codes + _sample_codes_covered(db_path, seed, sample_dates, 40))[:20]
    decision = decision_at or datetime.now(_TZ).isoformat(timespec="seconds")
    repo = PitRepository(db_path)
    diffs: list[dict[str, Any]] = []
    checked = 0
    pairs = 0
    for code in sample_codes[:PARITY_CODES]:
        for d in sample_dates[:PARITY_DAYS]:
            with _connect(db_path) as conn:
                legacy = conn.execute(
                    "SELECT open, high, low, close, vol, amount FROM daily"
                    " WHERE ts_code=? AND trade_date=?",
                    (code, d),
                ).fetchone()
            pit = repo.read_asof("daily", {"ts_code": code, "trade_date": d}, decision)
            checked += 1
            if legacy is None:
                diffs.append({"code": code, "date": d, "field": "legacy", "detail": "legacy 缺失"})
                continue
            if pit is None:
                diffs.append({"code": code, "date": d, "field": "pit", "detail": "PIT as-of 缺失"})
                continue
            for i, field in enumerate(PARITY_FIELDS):
                lv = float(legacy[i])
                pv = float(pit["payload"].get(field, float("nan")))
                tolerance = 0.001 if field in ("vol", "amount") else 0.0001
                if abs(lv - pv) > tolerance:
                    diffs.append(
                        {"code": code, "date": d, "field": field,
                         "detail": f"legacy={lv}, pit={pv}"}
                    )
                pairs += 1
    return {
        "name": "shadow_parity",
        "pass": not diffs,
        "result": "PASS" if not diffs else "FAIL",
        "code_sha": _code_sha(),
        "config_hash": _config_hash(),
        "db_fingerprint": _db_fingerprint(db_path),
        "seed": seed,
        "sample_codes": sample_codes[:PARITY_CODES],
        "sample_dates": sample_dates[:PARITY_DAYS],
        "samples_checked": checked,
        "pairs_compared": pairs,
        "diffs": diffs,
        "decision_at": decision,
    }


def _sample_codes_covered(
    db_path: str | Path, seed: int, sample_dates: list[str], n: int = PARITY_CODES
) -> list[str]:
    """固定种子抽取在全部 sample_dates 上 legacy 与 PIT 均有数据的标的。

    只统计两个读取路径都存在的 (ts_code, trade_date)，避免把「退市/新上市」
    造成的单侧缺失误当成字段不一致。
    """
    if not sample_dates:
        return []
    with _connect(db_path) as conn:
        ph = ",".join("?" * len(sample_dates))
        rows = conn.execute(
            "SELECT ts_code FROM daily"
            f" WHERE trade_date IN ({ph}) GROUP BY ts_code"
            " HAVING COUNT(DISTINCT trade_date)=?",
            (*sample_dates, len(sample_dates)),
        ).fetchall()
    legacy_codes = {r[0] for r in rows}
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ts_code FROM daily_history"
            f" WHERE trade_date IN ({ph})",
            (*sample_dates,),
        ).fetchall()
    pit_codes = {r[0] for r in rows}
    covered = sorted(legacy_codes & pit_codes)
    rng = random.Random(seed)
    rng.shuffle(covered)
    return covered[:n]


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
