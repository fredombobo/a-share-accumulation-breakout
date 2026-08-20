"""DSL 解释器：StrategyDSL + 特征面板 → 信号序列（docs §6.3）。

- eval_condition / evaluate：单条件/整组条件求值（op 全集 + ref 动态引用）
- run：全市场扫描（进程池），采样日评估 entry，输出 TradingSignal 列表
- 特征缺失处理：feature 不在面板 → False + 归入 warnings（pred.* 即此路径）
- 防连发：同一股票信号日间隔 < cooldown 交易日则跳过

错误：InterpreterError（未知 feature/op 已在 schema 拦截，这里兜底）。
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field

import pandas as pd

from logic_platform.dsl.schema import Condition, StrategyDSL

_LOGGER = logging.getLogger(__name__)

# 同票信号冷却（交易日）：避免状态延续导致连续开仓
COOLDOWN_DAYS = 5


class InterpreterError(RuntimeError):
    """解释器执行错误。"""


@dataclass
class EvalResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TradingSignal:
    ts_code: str
    as_of: str                      # 采样日 YYYY-MM-DD（评估日）
    signal_date: str                # 信号成立日 YYYY-MM-DD（次日开盘入场）
    state: str
    reasons: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    box: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


def build_feature_panel(
    df: pd.DataFrame, feats: pd.DataFrame, sig: dict, record
) -> dict:
    """从 _analyze_raw 产物构建解释器特征面板。

    特征缺失（NaN/None）→ 面板值 None（条件求值为 False，pred.* 同理）。
    """
    panel: dict = {}
    rec_state = record.state
    box = record.box or {}

    panel["structure.state"] = rec_state
    panel["structure.is_breakout"] = bool(record.is_breakout)
    panel["structure.box_high"] = box.get("high")
    panel["structure.box_low"] = box.get("low")
    panel["structure.box_mid"] = box.get("mid")
    panel["structure.box_amp"] = box.get("amp")
    panel["structure.box_days"] = box.get("days")
    panel["structure.box_quality"] = box.get("quality")
    panel["structure.breakout_date"] = record.breakout_date
    panel["structure.days_from_box_end"] = feats["days_from_box_end"].iloc[-1] \
        if "days_from_box_end" in feats.columns and feats["days_from_box_end"].notna().any() else None

    if len(feats):
        last = feats.iloc[-1]
        for col in ["vol_ma_ratio_5_20", "vol_percentile_60", "shrink_days",
                    "breakout_vol_mult", "amount_ratio", "vp_corr_20",
                    "ret_1", "ret_5", "ret_20", "atr_14", "dist_ma20",
                    "dist_ma60", "dist_high_60"]:
            if col in last.index:
                v = last[col]
                panel[col] = None if (v is None or pd.isna(v)) else float(v)

    if len(df):
        last = df.iloc[-1]
        panel["close"] = float(last["close"]) if pd.notna(last["close"]) else None
        panel["vol"] = float(last["vol"]) if pd.notna(last["vol"]) else None
    for key in ("ma5", "ma10", "ma20"):
        v = sig.get(key)
        panel[key] = None if v is None or pd.isna(v) else float(v)

    # Phase 2 ML 预留：pred.* 一律 None（条件不通过）
    for key in ("pred.p_up_5", "pred.p_up_10", "pred.p_up_20"):
        panel[key] = None
    return panel


class Interpreter:
    """DSL 解释器。"""

    # ── 条件求值 ──

    def _resolve_value(self, cond: Condition, panel: dict) -> float | None:
        if cond.ref:
            # ref 语义映射：box_mid → structure.box_mid（schema 里 ref 独立命名）
            key = {
                "box_mid": "structure.box_mid",
                "box_high": "structure.box_high",
                "box_low": "structure.box_low",
            }.get(cond.ref, cond.ref)
            v = panel.get(key)
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
        return cond.value

    def eval_condition(self, cond: Condition, panel: dict, warnings: list[str]) -> bool:
        v = panel.get(cond.feature, None)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            if cond.op == "is_nan":
                return True
            if cond.op == "not_nan":
                return False
            if cond.feature.startswith("pred."):
                warnings.append(f"{cond.feature} 不可用（ML 未启用，条件视为不通过）")
            return False
        if cond.op == "is_nan":
            return False
        if cond.op == "not_nan":
            return True

        try:
            if cond.op in ("in", "not_in"):
                hit = v in (cond.value or [])
                return hit if cond.op == "in" else not hit
            target = self._resolve_value(cond, panel)
            if target is None:
                warnings.append(f"{cond.feature} 的 ref/值不可用 → 条件视为不通过")
                return False
            fv, tv = float(v), float(target)
            return {
                ">=": fv >= tv, "<=": fv <= tv, ">": fv > tv,
                "<": fv < tv, "==": fv == tv, "!=": fv != tv,
            }[cond.op]
        except (TypeError, ValueError) as exc:
            warnings.append(f"{cond.feature} 求值异常（{exc}）→ 条件视为不通过")
            return False

    def evaluate(self, dsl: StrategyDSL, panel: dict) -> EvalResult:
        """整组条件求值：all 全过 且 any 至少一个（any 空组视为通过）。"""
        warnings: list[str] = []
        hit_all: list[str] = []
        for cond in dsl.entry.all:
            if not self.eval_condition(cond, panel, warnings):
                return EvalResult(False, warnings=warnings)
            hit_all.append(self._describe(cond, panel))

        hit_any: list[str] = []
        if dsl.entry.any:
            for cond in dsl.entry.any:
                if self.eval_condition(cond, panel, warnings):
                    hit_any.append(self._describe(cond, panel))
            if not hit_any:
                return EvalResult(False, warnings=warnings)

        reasons = hit_all + hit_any
        return EvalResult(True, reasons=reasons, warnings=warnings)

    @staticmethod
    def _describe(cond: Condition, panel: dict) -> str:
        v = panel.get(cond.feature)
        vs = f"{v:.2f}" if isinstance(v, float) else str(v)
        if cond.ref:
            rv = panel.get(cond.ref)
            rvs = f"{rv:.2f}" if isinstance(rv, float) else str(rv)
            return f"{cond.feature}={vs} {cond.op} ref:{cond.ref}={rvs}"
        return f"{cond.feature}={vs} {cond.op} {cond.value}"

    # ── 全市场扫描 ──

    def run(
        self,
        dsl: StrategyDSL,
        codes: list[str],
        store,
        workers: int = 4,
        progress_cb=None,
    ) -> dict:
        """扫描 universe → {signals: [TradingSignal.to_json], warnings, errors, scanned}。"""
        bt = dsl.params
        cal = _trade_cal(store, bt.start, bt.end)
        if not cal:
            return {"signals": [], "warnings": ["回测区间无交易日"], "errors": [], "scanned": 0}
        sample_days = cal[:: bt.step]
        early = cal[max(0, len(cal) - bt.lookback_bars - 30)]  # 区间首日往前补 lookback

        dsl_dict = dsl.model_dump()
        args = [(code, dsl_dict, bt.lookback_bars, early, bt.end,
                 sample_days, str(store.db_path)) for code in codes]

        results: list[list[dict] | None] = []
        if workers <= 1 or len(codes) <= 2:
            for i, a in enumerate(args, 1):
                results.append(_scan_stock(*a))
                if progress_cb and i % 25 == 0:
                    progress_cb(i, len(codes))
        else:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                for i, r in enumerate(ex.map(_scan_stock, *zip(*args)), 1):
                    results.append(r)
                    if progress_cb and i % 25 == 0:
                        progress_cb(i, len(codes))

        signals: list[dict] = []
        warnings: list[str] = []
        errors: list[str] = []
        for r in results:
            if not r:
                continue
            signals.extend(r.get("signals", []))
            warnings.extend(r.get("warnings", []))
            errors.extend(r.get("errors", []))
        signals.sort(key=lambda s: s["as_of"])
        return {"signals": signals, "warnings": warnings, "errors": errors,
                "scanned": len(codes)}


def _trade_cal(store, start: str, end: str) -> list[str]:
    """回测区间交易日（YYYYMMDD，升序）。"""
    try:
        cal = store._store.distinct_dates("daily")
    except Exception:  # noqa: BLE001
        return []
    return [d for d in cal if start <= d <= end]


def _scan_stock(
    code: str, dsl_dict: dict, lookback_bars: int, early: str, end: str,
    sample_days: list[str], db_path: str,
) -> dict | None:
    """进程池 worker：单票采样日扫描。"""
    try:
        from logic_platform.data.ab_store import ABStore
        from logic_platform.dsl.interpreter import Interpreter, build_feature_panel
        from logic_platform.dsl.schema import StrategyDSL
        from logic_platform.features.ohlcv_features import compute_ohlcv_features
        from logic_platform.features.volume_features import compute_volume_features
        from logic_platform.structure.state_machine import StateMachine
        from signals import detect_accumulation_breakout

        store = ABStore(db_path=db_path, migrate=False)
        dsl = StrategyDSL.model_validate(dsl_dict)
        df = store.ohlcv(code, start=early, end=end)
        if df is None or len(df) < 80:
            return None

        out_signals: list[dict] = []
        warnings: list[str] = []
        last_signal_date: str | None = None

        for day in sample_days:
            win = df[df["trade_date"] <= day].tail(lookback_bars)
            if len(win) < 60:
                continue

            sig_df = win.copy()
            sig_df["vol"] = pd.to_numeric(sig_df["vol"], errors="coerce").fillna(0.0)
            sig = detect_accumulation_breakout(sig_df)
            feats = compute_ohlcv_features(win, box=sig)
            feats = compute_volume_features(feats, box=sig)
            as_of = str(win["date"].iloc[-1])
            rec = StateMachine().evolve(win, sig, as_of=as_of)

            panel = build_feature_panel(win, feats, sig, rec)
            result = Interpreter().evaluate(dsl, panel)
            warnings.extend(result.warnings)

            if not result.passed:
                continue
            signal_date = rec.state_since or as_of
            # 防连发冷却
            if last_signal_date and signal_date <= last_signal_date:
                continue
            last_signal_date = signal_date

            out_signals.append({
                "ts_code": code, "as_of": as_of, "signal_date": signal_date,
                "state": rec.state, "reasons": result.reasons,
                "features": {k: v for k, v in panel.items() if v is not None},
                "box": rec.box,
            })
        return {"signals": out_signals, "warnings": warnings[:5], "errors": []}
    except Exception as exc:  # noqa: BLE001
        return {"signals": [], "warnings": [], "errors": [f"{code}: {exc}"]}
