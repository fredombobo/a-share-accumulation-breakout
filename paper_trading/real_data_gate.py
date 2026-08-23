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


def _benchmark_is_current(
    db_path: str | Path,
    expected_as_of: str,
    index_code: str = "000300.SH",
) -> tuple[bool, str]:
    """Return whether the local risk benchmark reaches the market data date."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM daily WHERE ts_code=?",
            (index_code,),
        ).fetchone()
        actual = str(row[0] or "") if row else ""
        return bool(expected_as_of and actual == expected_as_of), actual
    finally:
        conn.close()


def _code_version() -> str:
    """代码版本：包含未提交源码/前端产物变化的构建指纹。"""
    try:
        from build_version import build_version
        return build_version()
    except Exception:  # noqa: BLE001
        return "unknown"


def _is_valid_bar(
    open_price: float,
    high: float,
    low: float,
    close: float,
    vol: float,
    amount: float,
) -> bool:
    """接受正常 OHLC，也接受明确的停牌占位行（零价/零量但保留前收）。"""
    values = [open_price, high, low, close, vol, amount]
    try:
        o, h, lo, c, v, a = (float(x) for x in values)
    except (TypeError, ValueError):
        return False
    if v < 0 or a < 0 or c <= 0:
        return False
    if o == h == lo == 0 and v == 0 and a == 0:
        return True
    return o > 0 and h >= max(o, c) and lo <= min(o, c) and h >= lo


def _finalize_report(
    report: dict,
    db_path: Path,
    report_dir: str | Path | None,
) -> dict:
    """计算签名、写不可变文件并把同一报告登记到交易域。"""
    unsigned = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = _sha256_text(unsigned)
    if report_dir:
        rd = Path(report_dir)
        rd.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
        out = rd / f"real_data_gate_{stamp}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    try:
        from paper_trading.migrations import run_migrations
        run_migrations(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO pt_gate_report "
                "(run_date, passed, data_version, issues_json, report_json, code_version,"
                " config_hash, report_sha256, generated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now(_TZ).strftime("%Y%m%d"), 1 if report.get("passed") else 0,
                 str(report.get("local_latest_trade_date") or "n/a"),
                 json.dumps(report.get("issues") or [], ensure_ascii=False),
                 json.dumps(report, ensure_ascii=False), report.get("code_version"),
                 report.get("config_hash"), report["report_sha256"],
                 report.get("generated_at") or _now()),
            )
    except Exception as exc:  # noqa: BLE001
        from tushare_init import sanitize_error
        report.setdefault("persistence_warnings", []).append(sanitize_error(exc)[:200])
    return report


def run_gate(db_path: str | Path, days: int = 730, report_dir: str | Path | None = None) -> dict:
    """执行门禁。返回 {passed, issues[], report 字段...}。"""
    db_path = Path(db_path)
    issues: list[str] = []
    from tushare_init import resolve_token, sanitize_error

    token = resolve_token()
    if not token:
        return _finalize_report({
            "status": "NOT_RUN", "passed": False, "issues": ["无 TUSHARE_TOKEN"],
            "generated_at": _now(), "code_version": _code_version(),
            "config_hash": _config_hash(), "db_fingerprint": _db_fingerprint(db_path),
        }, db_path, report_dir)

    # 1) Tushare 接口可访问性 + 日历覆盖
    try:
        from tushare_init import get_pro
        pro = get_pro()
    except Exception as e:  # noqa: BLE001
        return _finalize_report({"status": "ERROR", "passed": False,
                "issues": [f"无法初始化数据适配器: {sanitize_error(e)}"],
                "generated_at": _now(), "code_version": _code_version(),
                "config_hash": _config_hash(), "db_fingerprint": _db_fingerprint(db_path)},
                db_path, report_dir)

    today = datetime.now(_TZ).date()
    start = today - timedelta(days=days * 2)
    print("[gate] 1/7 检查交易日历", file=sys.stderr)
    try:
        cal = pro.trade_cal(exchange="", start_date=start.strftime("%Y%m%d"),
                            end_date=today.strftime("%Y%m%d"), fields="cal_date,is_open")
        if cal is None or cal.empty or not {"cal_date", "is_open"}.issubset(cal.columns):
            raise RuntimeError("trade_cal 返回空或字段不完整")
        open_mask = cal["is_open"].astype(str).isin(("1", "1.0", "True", "true"))
        open_dates = sorted(cal.loc[open_mask, "cal_date"].astype(str).str[:8].tolist())
    except Exception as e:  # noqa: BLE001
        return _finalize_report({"status": "ERROR", "passed": False,
                "issues": [f"trade_cal 不可访问: {sanitize_error(e)}"],
                "generated_at": _now(), "code_version": _code_version(),
                "config_hash": _config_hash(), "db_fingerprint": _db_fingerprint(db_path)},
                db_path, report_dir)
    if len(open_dates) < days:
        issues.append(f"交易日历覆盖不足: {len(open_dates)} < {days}")

    # 2) 本地 daily 与数据源对齐（最新已完成交易日）
    print("[gate] 2/7 检查本地新鲜度和覆盖率", file=sys.stderr)
    conn = sqlite3.connect(str(db_path))
    local_max = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
    local_dates = {str(r[0]) for r in conn.execute("SELECT DISTINCT trade_date FROM daily")}
    # 最新已完成开市日（交易日 16:15 前不能视为已完成）。
    now = datetime.now(_TZ)
    if open_dates and open_dates[-1] == now.strftime("%Y%m%d") and (now.hour, now.minute) < (16, 15):
        open_dates = open_dates[:-1]
    last_open = open_dates[-1] if open_dates else ""
    if local_max != last_open:
        issues.append(f"本地 daily 最新 {local_max} ≠ 数据源 {last_open}")
    benchmark_ok, benchmark_as_of = _benchmark_is_current(db_path, str(local_max or ""))
    if not benchmark_ok:
        issues.append(f"风险基准 000300.SH 最新 {benchmark_as_of or '缺失'} ≠ 本地 daily {local_max}")
    try:
        source_benchmark = pro.index_daily(
            ts_code="000300.SH",
            start_date=last_open,
            end_date=last_open,
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if source_benchmark is None or source_benchmark.empty:
            issues.append(f"数据源风险基准 000300.SH 在 {last_open} 无行情")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"风险基准接口不可用: {sanitize_error(exc)}")

    # 3) 活跃标的覆盖率
    try:
        basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        if basic is None or basic.empty or "ts_code" not in basic.columns:
            raise RuntimeError("stock_basic 返回空或字段不完整")
        active_codes = set(basic["ts_code"].astype(str).tolist())
        local_latest_codes = {r[0] for r in conn.execute(
            "SELECT DISTINCT ts_code FROM daily WHERE trade_date=?", (local_max,)
        )}
        if active_codes:
            cov = len(local_latest_codes & active_codes) / len(active_codes)
            if cov < 0.98:
                issues.append(f"最新交易日活跃标的覆盖率 {cov:.1%} < 98%")
    except Exception as e:  # noqa: BLE001
        active_codes = set()
        local_latest_codes = set()
        issues.append(f"活跃标的覆盖检查失败: {sanitize_error(e)}")

    # 4) 数据质量：主键无重复 / OHLC 有效 / 量额非负
    print("[gate] 3/7 检查主键、OHLC 和时点元数据", file=sys.stderr)
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ts_code, trade_date, COUNT(*) c FROM daily"
        " GROUP BY ts_code, trade_date HAVING c>1)"
    ).fetchone()[0]
    if dup:
        issues.append(f"daily 主键重复 {dup} 组")
    bad_ohlc = 0
    for bar in conn.execute("SELECT open, high, low, close, vol, amount FROM daily"):
        if not _is_valid_bar(*bar):
            bad_ohlc += 1
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
        "SELECT COUNT(*) FROM daily WHERE effective_at IS NULL OR available_at IS NULL "
        "OR ingested_at IS NULL OR source IS NULL OR revision IS NULL"
    ).fetchone()[0]
    if no_meta:
        issues.append(f"{no_meta} 行缺元数据（available_at/source）")
    # 7) 固定种子抽样比对（按日期批量拉取，避免 100 次串行 API）。
    print("[gate] 4/7 执行固定种子行情比对", file=sys.stderr)
    seed_codes: list[str] = []
    seed_dates: list[str] = []
    sample_pairs = 0
    mismatches: list[str] = []
    try:
        positive_latest = {r[0] for r in conn.execute(
            "SELECT ts_code FROM daily WHERE trade_date=? AND open>0 AND vol>=0",
            (local_max,),
        )}
        candidates = positive_latest & active_codes if active_codes else positive_latest
        seed_codes = sorted(candidates,
                            key=lambda code: hashlib.sha256(code.encode()).hexdigest())[:20]
        seed_dates = sorted(local_dates)[-5:]
        if len(seed_codes) < 20 or len(seed_dates) < 5:
            issues.append(f"抽样规模不足: {len(seed_codes)} 标的 × {len(seed_dates)} 日期")
        fields = ("open", "high", "low", "close", "vol", "amount")
        for index, d in enumerate(seed_dates, start=1):
            print(f"[gate]   样本日期 {index}/{len(seed_dates)}: {d}", file=sys.stderr)
            src = pro.daily(trade_date=d,
                            fields="ts_code,trade_date,open,high,low,close,vol,amount")
            if src is None or src.empty or not {"ts_code", *fields}.issubset(src.columns):
                mismatches.append(f"{d}: 数据源返回空或字段不完整")
                continue
            source_rows = {str(row["ts_code"]): row for _, row in src.iterrows()
                           if str(row["ts_code"]) in seed_codes}
            for code in seed_codes:
                loc = conn.execute(
                    "SELECT open, high, low, close, vol, amount FROM daily "
                    "WHERE ts_code=? AND trade_date=?", (code, d)
                ).fetchone()
                source_row = source_rows.get(code)
                if loc is None:
                    mismatches.append(f"{code}@{d}: 本地缺失")
                    continue
                if source_row is None:
                    if _is_valid_bar(*loc) and float(loc[0]) == 0:
                        continue
                    mismatches.append(f"{code}@{d}: 数据源缺失")
                    continue
                sample_pairs += 1
                for field_index, field in enumerate(fields):
                    local_value = float(loc[field_index])
                    source_value = float(source_row[field])
                    tolerance = 0.001 if field in ("vol", "amount") else 0.0001
                    if abs(local_value - source_value) > tolerance:
                        mismatches.append(
                            f"{code}@{d} {field}: local={local_value}, source={source_value}"
                        )
        if mismatches:
            issues.append(f"种子抽样 {len(mismatches)} 处不一致: {mismatches[:8]}")
    except Exception as e:  # noqa: BLE001
        issues.append(f"种子抽样失败: {sanitize_error(e)}")

    # 8) 公司行为接口必须对持仓标的可用；无持仓时至少验证一次接口权限。
    # V2R-D：经由 tushare_pit 适配器拉取，无权限/异常显式失败（fail-closed）。
    print("[gate] 5/7 检查公司行为数据权限", file=sys.stderr)
    corporate_action_codes = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM pt_position_lot WHERE account_id=1 AND remaining_qty>0"
    )})
    if not corporate_action_codes and seed_codes:
        corporate_action_codes = [seed_codes[0]]
    corporate_action_checked = 0
    try:
        from ab_screener.data.adapters.tushare_pit import fetch_corporate_actions

        for code in corporate_action_codes:
            rows = fetch_corporate_actions(pro, ts_code=code)
            if rows is None:
                raise RuntimeError(f"{code} 公司行为返回 None")
            corporate_action_checked += 1
    except Exception as e:  # noqa: BLE001
        issues.append(f"公司行为接口不可用或无权限: {sanitize_error(e)}")
    conn.close()

    # P1.3 / V2R-D：v2 数据质量门禁（instrument 注册表迁移后激活；未迁移不阻断 legacy 门禁）
    try:
        with sqlite3.connect(str(db_path)) as _qc_conn:
            _has_universe = _qc_conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='instrument_universe_rules'"
            ).fetchone()
        if _has_universe:
            from ab_screener.application.data_quality import (
                run_data_quality as _dq,
            )
            from ab_screener.application.data_quality import (
                shadow_parity as _shadow_parity,
            )

            quality = _dq(
                db_path,
                as_of=str(local_max or ""),
                pro=pro,
                latest_trade_date=str(last_open or ""),
                seed_codes=seed_codes or [],
            )
            if quality["result"] != "PASS":
                issues.append(f"v2 数据质量 {quality['result']}: {quality['summary']}")
            # 影子 parity：legacy daily 与 PIT daily_history as-of 读取对比。
            # decision_at 用「现在」：验证当前两个读取路径一致（PIT 回填的
            # available_at 为入库时刻，不能用数据日期当 decision_at）。
            # 不传 codes/dates，让 parity 自行抽样「两个路径都有数据」的
            # 20 标的 × 5 日期；避免把「新上市无历史」误当成字段不一致。
            parity = _shadow_parity(
                db_path,
                seed=42,
                codes=None,
                dates=None,
                decision_at=_now(),
            )
            if parity["result"] != "PASS":
                issues.append(
                    f"shadow parity {parity['result']}: "
                    f"{len(parity['diffs'])} 处差异（样本 {parity['samples_checked']}）"
                )
    except Exception as e:  # noqa: BLE001
        issues.append(f"v2 数据质量检查异常: {sanitize_error(e)}")

    passed = not issues
    report = {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "issues": issues,
        "date_range": f"{start.isoformat()}~{today.isoformat()}",
        "trade_days_covered": len(open_dates),
        "sample_codes": len(seed_codes) if "seed_codes" in dir() else 0,
        "sample_dates": len(seed_dates) if "seed_dates" in dir() else 0,
        "sample_pairs_compared": sample_pairs,
        "sample_mismatches": len(mismatches),
        "corporate_action_codes_checked": corporate_action_checked,
        "local_latest_trade_date": local_max,
        "source_latest_completed_trade_date": last_open,
        "benchmark_code": "000300.SH",
        "benchmark_latest_trade_date": benchmark_as_of,
        "generated_at": _now(),
        "code_version": _code_version(),
        "config_hash": _config_hash(),
        "db_fingerprint": _db_fingerprint(db_path),
    }
    print("[gate] 6/7 固化报告签名", file=sys.stderr)
    finalized = _finalize_report(report, db_path, report_dir)
    print("[gate] 7/7 门禁完成", file=sys.stderr)
    return finalized


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
