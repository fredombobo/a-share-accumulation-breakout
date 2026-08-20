"""回测流水线（CLI 与 API 共用）：模板 → 扫描 → 回测 → 闸门 → 落库。

run_pipeline 是 run_logic_backtest.py 的核心逻辑抽取；CLI 与
POST /api/logic/backtest 都走这条链路，保证行为一致。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from logic_platform.backtest.engine import run_backtest
from logic_platform.backtest.gates import GateConfig, evaluate
from logic_platform.data.ab_store import ABStore
from logic_platform.dsl.interpreter import Interpreter
from logic_platform.dsl.parser import load_template

log = logging.getLogger("logic.pipeline")

_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = _ROOT / "runtime" / "stock_data.db"
TZ = UTC


def apply_overrides(dsl, overrides: list[str]) -> list[str]:
    """--set a.b.c=v 覆盖 pydantic 字段；返回错误列表（不抛）。"""
    errors: list[str] = []
    for ov in overrides:
        if "=" not in ov:
            errors.append(f"--set 格式错误（需 key=value）: {ov}")
            continue
        path, val = ov.split("=", 1)
        parts = path.split(".")
        if len(parts) < 2:
            errors.append(f"--set 路径至少两级（如 exit.stop_pct）: {ov}")
            continue
        section, field = parts[0], parts[-1]
        model = getattr(dsl, section, None)
        if model is None or field not in model.model_fields:
            errors.append(f"--set 未知字段: {path}")
            continue
        cur = getattr(model, field)
        try:
            if isinstance(cur, bool):
                setattr(model, field, val.lower() in ("1", "true", "yes"))
            elif isinstance(cur, int):
                setattr(model, field, int(val))
            else:
                setattr(model, field, float(val))
        except ValueError:
            errors.append(f"--set 数值解析失败: {path}={val}")
    return errors


def run_pipeline(
    template: str,
    params_overrides: dict | None = None,
    set_overrides: list[str] | None = None,
    gate_overrides: list[str] | None = None,
    progress_cb=None,
) -> dict:
    """完整闭环：模板 → 扫描 → 回测 → 闸门 → 落库 → 结果 dict。

    params_overrides: 直接覆盖 dsl.params 字段（start/end/step/max_codes/workers）
    set_overrides:  --set 风格（exit.stop_pct 等）
    gate_overrides: ["min_trades=20", ...]
    """
    t0 = time.time()
    dsl = load_template(template)

    for k, v in (params_overrides or {}).items():
        if hasattr(dsl.params, k):
            setattr(dsl.params, k, v)
    set_errors = apply_overrides(dsl, set_overrides or [])
    if set_errors:
        return {"error": "bad_params", "errors": set_errors}

    store = ABStore()
    codes = store.universe_from_stock_basic()[: dsl.params.max_codes]
    log.info("扫描 universe=%d 只（%s）", len(codes), template)

    def _cb(i, n):
        if progress_cb:
            progress_cb(i, n)
        else:
            log.info("扫描进度 %d/%d", i, n)

    scan = Interpreter().run(dsl, codes, store,
                             workers=dsl.params.workers, progress_cb=_cb)
    sigs = scan["signals"]

    bt = run_backtest(sigs, store, dsl.exit, dsl.id,
                      end=dsl.params.end, early=dsl.params.start,
                      lookback_bars=dsl.params.lookback_bars)

    gate_cfg = GateConfig.from_dict(_parse_gate(gate_overrides or []))
    gate = evaluate(bt.metrics, gate_cfg)

    _upsert_strategy(dsl, gate.status, gate.to_json())
    _upsert_backtest(dsl, bt, gate)

    return {
        "strategy_id": dsl.id,
        "version": dsl.version,
        "name": dsl.name,
        "research_only": dsl.research_only,
        "status": gate.status,
        "gate_passed": gate.passed,
        "signals_count": bt.signals_count,
        "metrics": bt.metrics,
        "gates": gate.to_json(),
        "run_id": bt.run_id,
        "params": {"start": dsl.params.start, "end": dsl.params.end,
                   "step": dsl.params.step, "max_codes": dsl.params.max_codes,
                   "stop_pct": dsl.exit.stop_pct, "target_pct": dsl.exit.target_pct,
                   "max_hold": dsl.exit.max_hold},
        "errors": (bt.errors or [])[:10],
        "scan_warnings": scan["warnings"][:10],
        "elapsed_sec": round(time.time() - t0, 1),
    }


def _parse_gate(items: list[str]) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        out[k] = float(v)
    return out


def _upsert_strategy(dsl, status: str, gate_json: dict) -> None:
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with sqlite3.connect(str(DB_PATH), timeout=30) as conn:
        conn.execute(
            """INSERT INTO logic_strategies (id, version, name, dsl_yaml, status,
               research_only, metrics_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 version=excluded.version, name=excluded.name, dsl_yaml=excluded.dsl_yaml,
                 status=excluded.status, metrics_json=excluded.metrics_json,
                 updated_at=excluded.updated_at""",
            (dsl.id, dsl.version, dsl.name, dsl.dsl_yaml, status,
             int(dsl.research_only), json.dumps(gate_json, ensure_ascii=False),
             now, now),
        )
        conn.commit()


def _upsert_backtest(dsl, bt, gate) -> None:
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with sqlite3.connect(str(DB_PATH), timeout=30) as conn:
        conn.execute(
            """INSERT INTO logic_backtests (run_id, strategy_id, params_json,
               window_json, metrics_json, equity_path, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET metrics_json=excluded.metrics_json""",
            (bt.run_id, dsl.id,
             json.dumps({"stop_pct": dsl.exit.stop_pct,
                         "target_pct": dsl.exit.target_pct,
                         "max_hold": dsl.exit.max_hold}, ensure_ascii=False),
             json.dumps({"start": dsl.params.start, "end": dsl.params.end},
                        ensure_ascii=False),
             json.dumps({**bt.metrics, "gate": gate.to_json(),
                         "signals_count": bt.signals_count},
                        ensure_ascii=False),
             None, now),
        )
        conn.commit()
