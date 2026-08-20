"""预测面板：特征 + 标签 → 训练集/测试集（docs §5.2/§5.3）。

- 数据源：宿主 SQLite（与 signals 同源，避免复权不一致；主文档 §5.3 优先级 1）
- 采样：状态过滤（默认仅 ACCUMULATION|TIGHTENING|BREAKOUT|FOLLOW_THROUGH，
  对齐 docs §5.2 第 4 条"仅在吸筹/突破附近推理"）
- 切分：严格时间序 walk-forward（70% 训练 / 30% OOS），禁止随机切分
- 特征列与 DSL FEATURE_NAMESPACES 对齐（pred 面板同源）
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from logic_platform.prediction.labels import add_labels

_LOGGER = logging.getLogger(__name__)

# 训练/推理聚焦状态（docs §5.2）
TRAIN_STATES = ["ACCUMULATION", "TIGHTENING", "BREAKOUT", "FOLLOW_THROUGH"]

# 特征列（与 dsl/schema.FEATURE_NAMESPACES 数值特征对齐）
FEATURE_COLS = [
    "vol_ma_ratio_5_20", "vol_percentile_60", "shrink_days",
    "breakout_vol_mult", "amount_ratio", "vp_corr_20",
    "ret_1", "ret_5", "ret_20", "atr_14", "dist_ma20", "dist_ma60",
    "dist_high_60", "box_amp", "days_from_box_end",
]

STATE_COLS = ["state_ACCUMULATION", "state_TIGHTENING", "state_BREAKOUT",
              "state_FOLLOW_THROUGH", "state_FAIL", "state_IDLE"]


def _panel_one_stock(
    code: str, start: str, end: str, lookback: int, horizon: int,
    states: list[str], db_path: str,
) -> list[dict]:
    """进程池 worker：单票面板行（模块级以便 pickle）。"""
    try:
        from logic_platform.data.ab_store import ABStore
        from logic_platform.features.ohlcv_features import compute_ohlcv_features
        from logic_platform.features.volume_features import compute_volume_features
        from logic_platform.structure.state_machine import StateMachine
        from signals import detect_accumulation_breakout

        store = ABStore(db_path=db_path, migrate=False)
        df = store.ohlcv(code, start=start, end=end, limit=lookback + 400)
        if df is None or len(df) < 120:
            return []
        df = df.sort_values("trade_date").reset_index(drop=True)

        sig0 = detect_accumulation_breakout(df.copy())
        feats = compute_ohlcv_features(df, box=sig0)
        feats = compute_volume_features(feats, box=sig0)
        feats = add_labels(feats, horizons=(horizon,))

        sm = StateMachine()
        rows: list[dict] = []
        step = 5
        for i in range(80, len(feats), step):
            win = feats.iloc[: i + 1]
            if len(win) < 80:
                continue
            sig_df = win[["date", "open", "high", "low", "close", "vol"]].copy()
            sig_df["vol"] = pd.to_numeric(sig_df["vol"], errors="coerce").fillna(0.0)
            sig = detect_accumulation_breakout(sig_df)
            as_of = str(win["date"].iloc[-1])
            rec = sm.evolve(win, sig, as_of=as_of)
            if rec.state not in states:
                continue
            row = feats.iloc[i]
            if pd.isna(row.get(f"y_ret_{horizon}")):
                continue
            feat = {c: (None if pd.isna(row[c]) else float(row[c])) for c in FEATURE_COLS}
            rows.append({
                "ts_code": code, "trade_date": row["trade_date"], "date": as_of,
                "state": rec.state,
                **feat,
                **{s: int(rec.state == s.replace("state_", "")) for s in STATE_COLS},
                f"y_up_{horizon}": float(row[f"y_up_{horizon}"]),
                f"y_ret_{horizon}": float(row[f"y_ret_{horizon}"]),
                f"y_mdd_{horizon}": float(row[f"y_mdd_{horizon}"]),
            })
        return rows
    except Exception:  # noqa: BLE001 —— 单票失败不拖垮整批
        return []


def build_panel(
    store,
    codes: list[str],
    start: str,
    end: str,
    lookback: int = 180,
    horizon: int = 10,
    states: list[str] | None = None,
    workers: int = 6,
) -> pd.DataFrame:
    """全市场特征+标签面板（按股票进程池并行）。"""
    states = states or TRAIN_STATES
    args = [(c, start, end, lookback, horizon, states, str(store.db_path))
            for c in codes]
    all_rows: list[dict] = []
    if workers <= 1 or len(codes) <= 2:
        for a in args:
            all_rows.extend(_panel_one_stock(*a))
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as ex:
            for rows in ex.map(_panel_one_stock, *zip(*args)):
                all_rows.extend(rows)

    panel = pd.DataFrame(all_rows)
    if panel.empty:
        return panel
    return panel.sort_values("trade_date").reset_index(drop=True)


def train_test_split_timewise(
    panel: pd.DataFrame, test_ratio: float = 0.3, horizon: int = 10
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """时间序切分（70/30）：按 trade_date 分位切，保证 OOS 全部晚于 IS。"""
    if panel.empty:
        return panel, panel
    dates = panel["trade_date"].unique()
    cut = dates[int(len(dates) * (1 - test_ratio))]
    is_df = panel[panel["trade_date"] < cut]
    oos_df = panel[panel["trade_date"] >= cut]
    return is_df.reset_index(drop=True), oos_df.reset_index(drop=True)


def to_matrix(panel: pd.DataFrame, horizon: int = 10):
    """面板 → (X, y_up, y_ret, y_mdd)。X 列顺序 = FEATURE_COLS + STATE_COLS。"""
    cols = FEATURE_COLS + STATE_COLS
    X = panel[cols].copy()
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return (
        X.values.astype(np.float32),
        panel[f"y_up_{horizon}"].values.astype(np.float32),
        panel[f"y_ret_{horizon}"].values.astype(np.float32),
        panel[f"y_mdd_{horizon}"].values.astype(np.float32),
    )
