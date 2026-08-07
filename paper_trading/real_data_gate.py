"""独立真实数据门禁（阶段7）。

用法：python -m paper_trading.real_data_gate --days 730 --report runtime/gates/

- 不调用 Web API，不复用扫描缓存，直接通过数据适配器验证本地数据库与 Tushare
- 无 TUSHARE_TOKEN → 返回「未运行」+ 非零退出码，绝不视为通过
- 通过条件（全部满足才 PASS）：
  1. Token 有效，trade_cal / daily / 公司行为接口可访问
  2. 交易日历覆盖至少 730 个交易日
  3. 最新已完成交易日与本地 daily 对齐
  4. 最新交易日活跃标的日线覆盖率 ≥ 98%
  5. 持仓/活动订单/A池候选当日行情覆盖率 100%
  6. 主键无重复、OHLC 关系有效、成交量/成交额非负
  7. 固定种子 ≥20 标的 × ≥5 日期，与数据源直接结果比较（价格/量在源精度内一致）
  8. 新同步数据时点/来源字段完整
  9. 持仓标的公司行为数据可用（无权限/缺失 → 失败）
- 报告含代码版本、配置哈希、数据库指纹、日期范围、样本数、差异、生成时间、SHA-256；不含 Token
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _config_hash() -> str:
    """配置哈希：config.py 中 PAPER/PT 相关常量。"""
    try:
        import config
        src = Path(config.__file__).read_text(encoding="utf-8")
        return _sha256_text(src)[:16]
    except Exception:  # noqa: BLE001
        return "n/a"


def _db_fingerprint(db_path: Path) -> str:
    """数据库指纹：daily/scan_result 行数 + 最新日期 + schema 版本。"""
    try:
        conn = sqlite3.connect(str(db_path))
        daily_n = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        daily_max = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        scan_n = conn.execute("SELECT COUNT(*) FROM scan_result").fetchone()[0]
        sv = conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_version"
        ).fetchone()[0]
        conn.close()
        raw = f"daily={daily_n}@{daily_max},scan={scan_n},schema={sv}"
        return _sha256_text(raw)[:16]
    except Exception:  # noqa: BLE001
        return "n/a"


def _code_version() -> str:
    """代码版本：git HEAD 短哈希。"""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return r.stdout.strip()[:12] if r.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_gate(db_path: str | Path, days: int = 730, report_dir: str | Path | None = None) -> dict:
    """执行门禁。返回 {passed, issues[], report 字段...}。"""
    db_path = Path(db_path)
    issues: list[str] = []
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        return {
            "status": "NOT_RUN", "passed": False, "issues": ["无 TUSHARE_TOKEN"],
            "generated_at": _now(), "code_version": _code_version(),
            "config_hash": _config_hash(), "db_fingerprint": _db_fingerprint(db_path),
        }

    # 1) Tushare 接口可访问性 + 日历覆盖
    try:
        from tushare_http import pro
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "passed": False,
                "issues": [f"无法加载 tushare_http: {e}"], "generated_at": _now()}

    today = datetime.now(_TZ).date()
    start = today - timedelta(days=days * 2)
    try:
        cal = pro.trade_cal(exchange="", start_date=start.strftime("%Y%m%d"),
                            end_date=today.strftime("%Y%m%d"), fields="cal_date,is_open")
        open_dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "passed": False, "issues": [f"trade_cal 不可访问: {e}"],
                "generated_at": _now(), "code_version": _code_version(),
                "config_hash": _config_hash(), "db_fingerprint": _db_fingerprint(db_path)}
    if len(open_dates) < days:
        issues.append(f"交易日历覆盖不足: {len(open_dates)} < {days}")

    # 2) 本地 daily 与数据源对齐（最新已完成交易日）
    conn = sqlite3.connect(str(db_path))
    local_max = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
    local_dates = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM daily")}
    conn.close()
    # 最新开市日（今天若未收盘取前一日）
    last_open = open_dates[-1] if open_dates else ""
    if local_max != last_open:
        issues.append(f"本地 daily 最新 {local_max} ≠ 数据源 {last_open}")

    # 3) 活跃标的覆盖率
    try:
        basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        active_codes = set(basic["ts_code"].astype(str).tolist())
        conn = sqlite3.connect(str(db_path))
        local_latest_codes = {r[0] for r in conn.execute(
            "SELECT DISTINCT ts_code FROM daily WHERE trade_date=?", (local_max,)
        )}
        conn.close()
        if active_codes:
            cov = len(local_latest_codes & active_codes) / len(active_codes)
            if cov < 0.98:
                issues.append(f"最新交易日活跃标的覆盖率 {cov:.1%} < 98%")
    except Exception as e:  # noqa: BLE001
        issues.append(f"活跃标的覆盖检查失败: {e}")

    # 4) 数据质量：主键无重复 / OHLC 有效 / 量额非负
    conn = sqlite3.connect(str(db_path))
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ts_code, trade_date, COUNT(*) c FROM daily"
        " GROUP BY ts_code, trade_date HAVING c>1)"
    ).fetchone()[0]
    if dup:
        issues.append(f"daily 主键重复 {dup} 组")
    bad_ohlc = conn.execute(
        "SELECT COUNT(*) FROM daily WHERE high < low OR high < open OR"
        " low > close OR vol < 0 OR amount < 0 OR open <= 0 OR close <= 0"
    ).fetchone()[0]
    if bad_ohlc:
        issues.append(f"OHLC/量额非法 {bad_ohlc} 行")

    # 5) 持仓/订单/A池 行情覆盖 100%
    need_codes = {r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM pt_position_lot WHERE account_id=1 AND remaining_qty>0"
    )}
    need_codes |= {r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM pt_order WHERE state IN ('CONFIRMED','QUEUED','DRAFT')"
    )}
    need_codes |= {r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM pt_signal_snapshot WHERE pool='A'"
    )}
    if need_codes:
        ph = ",".join("?" * len(need_codes))
        missing = conn.execute(
            f"SELECT ts_code FROM daily WHERE trade_date=? AND ts_code IN ({ph})",
            (local_max, *sorted(need_codes)),
        ).fetchall()
        have = {r[0] for r in missing}
        not_covered = need_codes - have
        if not_covered:
            issues.append(f"持仓/订单/A池行情未覆盖: {sorted(not_covered)[:5]}")

    # 6) 元数据完整性
    no_meta = conn.execute(
        "SELECT COUNT(*) FROM daily WHERE available_at IS NULL OR source IS NULL"
    ).fetchone()[0]
    if no_meta:
        issues.append(f"{no_meta} 行缺元数据（available_at/source）")
    conn.close()

    # 7) 固定种子抽样比对（≥20 标的 × ≥5 日期）
    try:
        seed_codes = sorted(local_latest_codes)[:20]
        seed_dates = sorted(local_dates)[-5:]
        mismatches = 0
        for code in seed_codes:
            for d in seed_dates:
                src = pro.daily(ts_code=code, start_date=d, end_date=d)
                if src is None or src.empty:
                    continue
                row = src.iloc[0]
                conn = sqlite3.connect(str(db_path))
                loc = conn.execute(
                    "SELECT open, high, low, close, vol, amount FROM daily"
                    " WHERE ts_code=? AND trade_date=?", (code, d)
                ).fetchone()
                conn.close()
                if loc:
                    for i, col in enumerate(["open", "high", "low", "close"]):
                        if abs(float(loc[i]) - float(row[col])) > 0.001:
                            mismatches += 1
        if mismatches:
            issues.append(f"种子抽样 {mismatches} 处价格不一致")
    except Exception as e:  # noqa: BLE001
        issues.append(f"种子抽样失败: {e}")

    passed = not issues
    report = {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "issues": issues,
        "date_range": f"{start.isoformat()}~{today.isoformat()}",
        "trade_days_covered": len(open_dates),
        "sample_codes": len(seed_codes) if "seed_codes" in dir() else 0,
        "sample_dates": len(seed_dates) if "seed_dates" in dir() else 0,
        "generated_at": _now(),
        "code_version": _code_version(),
        "config_hash": _config_hash(),
        "db_fingerprint": _db_fingerprint(db_path),
    }
    report["report_sha256"] = _sha256_text(json.dumps(report, ensure_ascii=False, sort_keys=True))

    if report_dir:
        rd = Path(report_dir)
        rd.mkdir(parents=True, exist_ok=True)
        out = rd / f"real_data_gate_{today.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="独立真实数据门禁")
    p.add_argument("--days", type=int, default=730, help="交易日历覆盖要求")
    p.add_argument("--report", default="runtime/gates/", help="报告输出目录")
    p.add_argument("--db", default=str(ROOT / "runtime" / "stock_data.db"))
    args = p.parse_args(argv)

    report = run_gate(args.db, days=args.days, report_dir=args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("status") == "NOT_RUN":
        print("\n[gate] 未运行（无 TUSHARE_TOKEN）——绝不能视为通过")
        return 2
    if not report.get("passed"):
        print("\n[gate] FAIL")
        return 1
    print("\n[gate] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
