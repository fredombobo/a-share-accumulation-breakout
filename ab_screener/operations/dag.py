"""持久每日 DAG（V2R-O2）：冻结 9 步 EOD 合同 + 显式依赖 + 生产 factory 接线。

- 冻结顺序（§4）：交易日/数据新鲜度/公司行为门禁 → 释放到期可卖批次 → 撮合
  此前确认订单 → 收盘估值 → 风险与损益快照 → 内部对账 → 信号 outcome 回填 →
  读取当日信号生成下一交易日草稿 → 固化 daily manifest。
- 租约、审计、告警与备份校验属于包围控制（SchedulerRunner / audit_service /
  alerts / backup），不插入业务因果链。
- 幂等键：trade_date + step_name + scope_type + scope_id + input_hash；相同键最多成功一次。
- max_attempts=3（含首次）；保留每次 attempt；崩溃续跑从已记录 attempt 之后继续。
- 上游 FAIL 阻断全部依赖下游；估值失败不得对账为通过，对账失败不得生成草稿/COMPLETE manifest。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json

_TZ = ZoneInfo("Asia/Shanghai")

# 唯一 EOD 业务顺序（§4 冻结）：9 步，线性因果链；不得按列表位置约定俗成。
DAG_STEPS: tuple[str, ...] = (
    "eod_gates",                 # 1 交易日/数据新鲜度/未处理公司行为门禁
    "release_matured_lots",      # 2 释放到期可卖批次
    "match_confirmed_orders",    # 3 撮合此前确认订单
    "close_valuation",           # 4 收盘估值
    "risk_pnl_snapshot",         # 5 风险与损益快照
    "internal_reconciliation",   # 6 内部对账
    "outcome_backfill",          # 7 信号 outcome 回填
    "generate_drafts",           # 8 读取当日信号生成下一交易日草稿
    "daily_manifest",            # 9 固化 daily manifest
)

# 显式依赖边：每步只依赖前一步（业务因果顺序固定）。
DEPENDENCY_EDGES: dict[str, tuple[str, ...]] = {
    "eod_gates": (),
    "release_matured_lots": ("eod_gates",),
    "match_confirmed_orders": ("release_matured_lots",),
    "close_valuation": ("match_confirmed_orders",),
    "risk_pnl_snapshot": ("close_valuation",),
    "internal_reconciliation": ("risk_pnl_snapshot",),
    "outcome_backfill": ("internal_reconciliation",),
    "generate_drafts": ("outcome_backfill",),
    "daily_manifest": ("generate_drafts",),
}

MAX_ATTEMPTS = 3
SCOPE_TYPES = ("GLOBAL", "ACCOUNT", "PROFILE")

# outcome 回填成本率（双边，含佣金与印花税近似；与生产接线口径一致）
OUTCOME_COST_RATE = 0.0013


class DagError(ValueError):
    """DAG 输入非法（fail-closed）。"""


class DagStepError(RuntimeError):
    """业务步骤失败（结构化错误：code + details 供下游阻断与告警）。"""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


def idempotency_key(
    trade_date: str, step_name: str, scope_type: str, scope_id: str, input_hash: str
) -> str:
    if scope_type not in SCOPE_TYPES:
        raise DagError(f"非法 scope_type: {scope_type}")
    blob = canonical_json({
        "trade_date": trade_date, "step": step_name,
        "scope_type": scope_type, "scope_id": scope_id, "input": input_hash,
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StepSpec:
    name: str
    scope_type: str
    scope_id: str
    fn: Callable[..., Any]
    depends_on: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


class DailyDag:
    """按显式依赖顺序执行步骤；崩溃续跑从已记录 attempt 继续。"""

    def __init__(self, steps: list[StepSpec], max_attempts: int = MAX_ATTEMPTS):
        if not steps:
            raise DagError("DAG 至少需要一个步骤")
        names = [s.name for s in steps]
        if len(set(names)) != len(names):
            raise DagError("步骤名不能重复")
        self.steps = steps
        self.max_attempts = max_attempts

    def order(self) -> list[str]:
        return [s.name for s in self.steps]

    def dependencies_of(self, name: str) -> tuple[str, ...]:
        for s in self.steps:
            if s.name == name:
                return s.depends_on
        raise DagError(f"未知步骤: {name}")

    def validate_contract(self) -> None:
        """断言本 DAG 与冻结 9 步合同一致（顺序 + 每条依赖边）。"""
        if self.order() != list(DAG_STEPS):
            raise DagError(f"DAG 步骤与冻结合同不一致: {self.order()}")
        for step in self.steps:
            if step.depends_on != DEPENDENCY_EDGES[step.name]:
                raise DagError(
                    f"步骤 {step.name} 依赖边 {step.depends_on} != 合同 "
                    f"{DEPENDENCY_EDGES[step.name]}"
                )


# ── 生产 EOD factory：把真实服务按冻结合同接线（单账户 account_id=1） ──


def _require_table(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _step_eod_gates(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """门禁 1：交易日、数据新鲜度、未处理公司行为（fail-closed）。"""
    from ab_screener.application.corporate_action_service import blocking_summary
    from paper_trading.cal import is_open
    from paper_trading.settlement import get_positions

    db = ctx["db_path"]
    if not is_open(db, trade_date):
        raise DagStepError("NOT_TRADING_DAY", f"{trade_date} 不是交易日")
    with sqlite3.connect(db) as conn:
        has_market = conn.execute(
            "SELECT 1 FROM daily WHERE trade_date=? LIMIT 1", (trade_date,)
        ).fetchone()
    if not has_market:
        raise DagStepError(
            "NO_MARKET_DATA", f"{trade_date} 无行情数据（数据新鲜度门禁）"
        )
    holdings = [p["ts_code"] for p in get_positions(db)]
    if holdings:
        ca = blocking_summary(db, holdings, trade_date)
        if ca["blocked"]:
            raise DagStepError(
                "CORPORATE_ACTION_PENDING", ca["message"],
                details={"actions": ca["actions"], "gate_active": ca["gate_active"]},
            )
    return {
        "gates": "PASS", "open": True, "has_market_data": True,
        "corp_action_blocked": False, "holdings": len(holdings),
    }


def _step_release_matured_lots(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """释放到期可卖批次（幂等）。

    本系统 T+1 可卖由 pt_position_lot.sellable_date 派生；该步骤显式枚举
    "本日已到期"（sellable_date <= trade_date）批次并做负持仓一致性断言。
    同输入重跑结果一致，不新增账本行。
    """
    db = ctx["db_path"]
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT lot_id, ts_code, remaining_qty FROM pt_position_lot"
            " WHERE account_id=1 AND remaining_qty>0 AND sellable_date<=?"
            " ORDER BY lot_id",
            (trade_date,),
        ).fetchall()
        neg = conn.execute(
            "SELECT COUNT(*) FROM pt_position_lot WHERE remaining_qty<0"
        ).fetchone()[0]
    if neg:
        raise DagStepError(
            "NEGATIVE_POSITION", f"存在 {neg} 个负持仓批次，释放门禁失败"
        )
    return {
        "released_lots": len(rows),
        "released_codes": sorted({r[1] for r in rows}),
    }


def _step_match_confirmed_orders(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """撮合 3：撮合此前确认订单 + 日终过期 + 日结循环状态 PRE_OPEN。"""
    from paper_trading.engine import execute_fills, expire_daily_orders

    db = ctx["db_path"]
    today = ctx.get("today") or trade_date
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO pt_cycle (cycle_id, run_date, phase, retry_count, data_version,"
            " blocked_reason, started_at, finished_at)"
            " VALUES (?,?,'PRE_OPEN',0,?,NULL,?,NULL)"
            " ON CONFLICT(cycle_id) DO UPDATE SET phase='PRE_OPEN',"
            " retry_count=pt_cycle.retry_count+1, blocked_reason=NULL, finished_at=NULL",
            (f"CY-{trade_date}", trade_date, f"daily:{trade_date}", _now()),
        )
        conn.commit()
    fills = execute_fills(db, trade_date, today=today)
    expired = expire_daily_orders(db, trade_date)
    return {
        "filled": len(fills["filled"]),
        "zero_fill": len(fills["zero_fill"]),
        "expired": expired,
    }


def _step_close_valuation(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """估值 4：按当日收盘价估值（缺行情 → NO_VALUATION 阻断下游）。"""
    from paper_trading.settlement import mark_to_market

    mark = mark_to_market(ctx["db_path"], trade_date)
    return {"mark": mark}


def _step_risk_pnl_snapshot(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """快照 5：固化损益快照和同一时点的不可变组合风险快照。"""
    from ab_screener.application.portfolio_risk import build_portfolio_risk_report
    from ab_screener.data.risk_repository import save_risk_snapshot
    from paper_trading.risk_adapter import build_portfolio_state

    db = ctx["db_path"]
    mark = _mark_for_context(trade_date, ctx)
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO pt_daily_snapshot (account_id, trade_date, cash_fen,"
            " market_value_fen, total_asset_fen, realized_pnl_fen, unrealized_pnl_fen,"
            " drawdown_fen, positions_json)"
            " VALUES (1,?,?,?,?,0,?,NULL,?)"
            " ON CONFLICT(account_id, trade_date) DO UPDATE SET"
            " cash_fen=excluded.cash_fen, market_value_fen=excluded.market_value_fen,"
            " total_asset_fen=excluded.total_asset_fen,"
            " realized_pnl_fen=excluded.realized_pnl_fen,"
            " unrealized_pnl_fen=excluded.unrealized_pnl_fen,"
            " drawdown_fen=excluded.drawdown_fen, positions_json=excluded.positions_json",
            (trade_date, mark["cash_fen"], mark["market_value_fen"],
             mark["total_asset_fen"], mark["unrealized_pnl_fen"],
             json.dumps(mark["holdings"], ensure_ascii=False)),
        )
        conn.commit()

    state = build_portfolio_state(db, today=trade_date)
    if state.equity_fen != int(mark["total_asset_fen"]):
        raise DagStepError(
            "RISK_VALUATION_MISMATCH",
            "风险状态权益与日终估值不一致",
            details={
                "risk_equity_fen": state.equity_fen,
                "mark_total_asset_fen": int(mark["total_asset_fen"]),
            },
        )
    with sqlite3.connect(db) as conn:
        equity_curve = [
            int(row[0])
            for row in conn.execute(
                "SELECT total_asset_fen FROM pt_daily_snapshot"
                " WHERE account_id=1 AND trade_date<=? ORDER BY trade_date",
                (trade_date,),
            ).fetchall()
        ]
        missing_capacity: list[str] = []
        capacity_fen = 0
        for holding in mark["holdings"]:
            amounts = conn.execute(
                "SELECT amount FROM daily WHERE ts_code=? AND trade_date<=?"
                " AND amount IS NOT NULL AND amount>0"
                " ORDER BY trade_date DESC LIMIT 20",
                (holding["ts_code"], trade_date),
            ).fetchall()
            if not amounts:
                missing_capacity.append(str(holding["ts_code"]))
                continue
            # Tushare daily.amount 单位为千元；按冻结 5% 参与率换算为分。
            adv20_thousand_yuan = sum(
                (Decimal(str(row[0])) for row in amounts), start=Decimal(0)
            ) / Decimal(len(amounts))
            capacity_fen += int(
                (adv20_thousand_yuan * Decimal(1000) * Decimal(100)
                 * Decimal("0.05")).to_integral_value(rounding=ROUND_FLOOR)
            )

    total_asset_fen = int(mark["total_asset_fen"])
    position_weights = (
        [float(Decimal(int(item["market_value_fen"])) / Decimal(total_asset_fen))
         for item in mark["holdings"]]
        if total_asset_fen > 0 else []
    )
    report = build_portfolio_risk_report(
        state,
        equity_curve=[float(value) for value in equity_curve],
        position_weights=position_weights,
        daily_capacity_fen=capacity_fen,
    )
    report["inputs"] = {
        "trade_date": trade_date,
        "market_version": f"daily:{trade_date}",
        "equity_points": len(equity_curve),
        "position_weights": position_weights,
        "daily_capacity_fen": capacity_fen,
        "capacity_missing_codes": missing_capacity,
        "participation_bps": 500,
        "amount_unit": "thousand_yuan",
    }
    component_statuses = [
        str(report["metrics"].get("status") or "INSUFFICIENT"),
        str(report["concentration"].get("status") or "INSUFFICIENT"),
    ]
    report["status"] = (
        "OK"
        if all(status == "OK" for status in component_statuses) and not missing_capacity
        else "INSUFFICIENT"
    )
    scenarios = dict(report.pop("scenarios"))
    with sqlite3.connect(db) as conn:
        risk_snapshot_id = save_risk_snapshot(
            conn,
            trade_date=trade_date,
            market_version=f"daily:{trade_date}",
            metrics=report,
            scenarios=scenarios,
        )
    return {
        "snapshot_ok": True,
        "risk_snapshot_id": risk_snapshot_id,
        "risk_status": report["status"],
        "total_asset_fen": mark["total_asset_fen"],
        "unrealized_pnl_fen": mark["unrealized_pnl_fen"],
    }


def _step_internal_reconciliation(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """对账 6：内部对账（阻断级）；DIFF → 置 RECONCILE 并抛错阻断全部下游。"""
    from paper_trading.settlement import run_reconciliation

    db = ctx["db_path"]
    mark = _mark_for_context(trade_date, ctx)
    rec = run_reconciliation(db, trade_date, expected_mark=mark)
    if rec["result"] != "OK":
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE pt_cycle SET phase='RECONCILE', blocked_reason=?,"
                " finished_at=NULL WHERE cycle_id=?",
                (json.dumps(rec["diffs"], ensure_ascii=False), f"CY-{trade_date}"),
            )
            conn.commit()
        raise DagStepError(
            "RECONCILIATION_DIFF", "内部对账差异（阻断），不发布草稿与 manifest",
            details=rec["diffs"],
        )
    return {"result": "OK", "diffs": rec["diffs"]}


def _advance_trading_days(db: str | Path, start: str, days: int) -> str:
    current = start
    for _ in range(days):
        current = _next_open_after(db, current)
    return current


def _next_open_after(db: str | Path, trade_date: str) -> str:
    """返回严格晚于 trade_date 的下一开市日，避免含当日 next_open 停滞。"""
    from paper_trading.cal import next_open

    current = datetime.strptime(trade_date, "%Y%m%d").date() + timedelta(days=1)
    return next_open(db, current.strftime("%Y%m%d"))


def _mark_for_context(trade_date: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """恢复估值步骤结果。

    调度进程可能在 close_valuation 成功落 attempt 后崩溃。重启时该步骤会按幂等键
    跳过，因此不能依赖上一个进程的内存 ctx；缺失时从同一账本和收盘行情重新估值。
    """
    cached = (ctx.get("results") or {}).get("close_valuation") or {}
    mark = cached.get("mark")
    if isinstance(mark, dict):
        return mark
    from paper_trading.settlement import mark_to_market

    return mark_to_market(ctx["db_path"], trade_date)


def _price_micro(value: Any) -> int:
    """把 SQLite 行情值精确转换为微元，禁止二进制浮点参与定点换算。"""
    return int(
        (Decimal(str(value)) * Decimal(1_000_000)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )


def _close_quote_micro(
    db: str | Path, ts_code: str, trade_date: str
) -> tuple[int | None, str | None]:
    """读取收盘价与真实可用时点；缺时点时保守返回 PENDING 所需的 None。"""
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT close, available_at FROM daily WHERE ts_code=? AND trade_date=?",
            (ts_code, trade_date),
        ).fetchone()
    if row is None or row[0] is None:
        return None, None
    return _price_micro(row[0]), (str(row[1]) if row[1] else None)


def _entry_price_micro(
    db: str | Path,
    ts_code: str,
    signal_date: str,
    entry_date: str,
) -> int | None:
    """理论入场价：指定信号在下一开市日的实际 SIGNAL 成交价。"""
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT f.fill_price_micro FROM pt_fill f"
            " JOIN pt_order o ON o.order_id=f.order_id"
            " WHERE o.ts_code=? AND o.side='BUY' AND o.source='SIGNAL'"
            " AND o.signal_trade_date=? AND o.eligible_trade_date=?"
            " ORDER BY f.filled_at LIMIT 1",
            (ts_code, signal_date, entry_date),
        ).fetchone()
    return int(row[0]) if row else None


def _step_outcome_backfill(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """回填 7：对已到期信号 observation 的 5/10/20 日 outcome 调真实服务回填。

    真实服务 `backfill_horizon_outcome` 保证修订追加、重放幂等；缺入场成交 →
    UNFILLABLE（收益 NULL 不填 0）；交易日未完成/行情未 available → PENDING。
    """
    from ab_screener.application.signal_outcomes import HORIZONS, backfill_horizon_outcome
    db = ctx["db_path"]
    calculation_at = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T16:30:00+08:00"
    with sqlite3.connect(db) as conn:
        if not _require_table(conn, "signal_observations"):
            return {"processed": 0, "details": [], "wired": False}
        rows = conn.execute(
            "SELECT observation_id, ts_code, signal_date FROM signal_observations"
            " WHERE signal_date<=? ORDER BY signal_date, observation_id",
            (trade_date,),
        ).fetchall()

    processed: list[dict[str, Any]] = []
    for observation_id, ts_code, signal_date in rows:
        try:
            entry_date = _next_open_after(db, signal_date)
        except Exception:  # noqa: BLE001, S112 交易日历缺失 → 跳过该 observation
            continue
        entry_micro = _entry_price_micro(db, ts_code, signal_date, entry_date)
        for horizon in HORIZONS:
            try:
                maturity = _advance_trading_days(db, entry_date, horizon)
            except Exception:  # noqa: BLE001, S112 交易日历尚未覆盖成熟日，保守等待
                continue
            if maturity > trade_date:
                continue
            exit_micro, data_available_at = _close_quote_micro(db, ts_code, maturity)
            with sqlite3.connect(db) as conn:
                result = backfill_horizon_outcome(
                    conn,
                    observation_id=observation_id,
                    horizon_days=horizon,
                    entry_price_micro=entry_micro,
                    cost_rate=OUTCOME_COST_RATE,
                    maturity_trade_date=maturity,
                    last_completed_trade_date=trade_date,
                    calculation_at=calculation_at,
                    exit_price_micro=exit_micro,
                    data_available_at=data_available_at,
                )
            processed.append({
                "observation_id": observation_id, "horizon_days": horizon,
                "status": result["status"],
                "idempotent": bool(result.get("idempotent", False)),
            })
    return {"processed": len(processed), "details": processed, "wired": True}


def _step_generate_drafts(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """草稿 8：读取当日信号快照 → 固化信号快照 → 生成下一交易日买入草稿。

    幂等短路：pt_cycle 已 DONE（前次成功）→ 跳过，不重复生成草稿。
    """
    from paper_trading.signals import generate_signal_drafts, sync_signal_snapshots

    db = ctx["db_path"]
    today = ctx.get("today") or trade_date
    with sqlite3.connect(db) as conn:
        cycle = conn.execute(
            "SELECT phase FROM pt_cycle WHERE run_date=?", (trade_date,)
        ).fetchone()
    if cycle and cycle[0] == "DONE":
        return {"idempotent": True, "skipped": "CYCLE_DONE"}
    signal_sync = sync_signal_snapshots(db, trade_date)
    drafts = generate_signal_drafts(db, trade_date, today=today)
    # 日结循环状态 DONE（业务业务完成；manifest 固化前最后一步）
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pt_cycle SET phase='DONE', data_version=?, blocked_reason=NULL,"
            " finished_at=? WHERE cycle_id=?",
            (f"daily:{trade_date}", _now(), f"CY-{trade_date}"),
        )
        conn.commit()
    return {
        "signals": signal_sync,
        "drafts": {"created": len(drafts["created"]), "rejected": len(drafts["rejected"])},
    }


def _step_daily_manifest(trade_date: str, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """固化 9：写 append-only daily manifest；非 COMPLETE → 结构化失败。"""
    from ab_screener.application.daily_manifest import create_daily_manifest

    db = ctx["db_path"]
    try:
        manifest = create_daily_manifest(db, trade_date)
    except Exception as exc:
        _mark_manifest_blocked(db, trade_date, f"MANIFEST_ERROR: {exc}")
        raise
    if manifest["status"] != "COMPLETE":
        _mark_manifest_blocked(
            db,
            trade_date,
            f"MANIFEST_NOT_COMPLETE: {manifest['blockers']}",
        )
        raise DagStepError(
            "MANIFEST_NOT_COMPLETE",
            f"daily manifest 非 COMPLETE（blockers: {manifest['blockers']}）",
            details={"blockers": manifest["blockers"]},
        )
    return {
        "status": manifest["status"],
        "manifest_sha256": manifest["manifest_sha256"],
        "blockers": manifest["blockers"],
    }


def _mark_manifest_blocked(db: str | Path, trade_date: str, reason: str) -> None:
    """manifest 未完成时撤销纸面 DONE 投影，避免 UI/调度误报日结完成。"""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pt_cycle SET phase='RECONCILE', blocked_reason=?, finished_at=NULL"
            " WHERE cycle_id=?",
            (reason[:2000], f"CY-{trade_date}"),
        )
        conn.commit()


def build_eod_dag(db_path: str | Path, *, today: str | None = None) -> DailyDag:
    """生产 EOD factory：按冻结 9 步合同把真实服务接线为单账户日终闭环。

    db_path 仅在建 DAG 后由各步骤经 ctx 使用；本函数不做任何数据库写入。
    today 用于历史交易日回放时注入确定时点。
    """
    steps = [
        StepSpec("eod_gates", "ACCOUNT", "1", _step_eod_gates),
        StepSpec("release_matured_lots", "ACCOUNT", "1", _step_release_matured_lots),
        StepSpec("match_confirmed_orders", "ACCOUNT", "1", _step_match_confirmed_orders),
        StepSpec("close_valuation", "ACCOUNT", "1", _step_close_valuation),
        StepSpec("risk_pnl_snapshot", "ACCOUNT", "1", _step_risk_pnl_snapshot),
        StepSpec("internal_reconciliation", "ACCOUNT", "1", _step_internal_reconciliation),
        StepSpec("outcome_backfill", "ACCOUNT", "1", _step_outcome_backfill),
        StepSpec("generate_drafts", "ACCOUNT", "1", _step_generate_drafts),
        StepSpec("daily_manifest", "ACCOUNT", "1", _step_daily_manifest),
    ]
    return DailyDag(
        [StepSpec(s.name, s.scope_type, s.scope_id, s.fn, DEPENDENCY_EDGES[s.name])
         for s in steps]
    )
