"""服务编排：单票分析（特征 + 状态机 + explain）的组合逻辑。

保持 api/routes.py 薄：路由只做参数/响应，业务在这里。
"""
from __future__ import annotations

import logging

import pandas as pd

from logic_platform.features.ohlcv_features import compute_ohlcv_features
from logic_platform.features.volume_features import compute_volume_features
from logic_platform.structure.state_machine import StateMachine

_LOGGER = logging.getLogger(__name__)

# 拉取 K 线回看（须 >= HORIZON_DAYS=160；180 留余量）
LOOKBACK_BARS = 180
# 特征序列回放窗口（前端展示）
SERIES_WINDOW = 30

_SUGGESTED_DSL = {
    "BREAKOUT": "vol_breakout_v1",
    "FOLLOW_THROUGH": "vol_breakout_v1",
    "ACCUMULATION": "vol_breakout_v1",
    "TIGHTENING": "vol_breakout_v1",
    "FAIL": None,
    "IDLE": None,
}

# 预测模型（模块级单例；无模型时 None，调用方降级）
_PREDICTOR = None


def get_predictor():
    """最新模型 Predictor；无模型返回 None（不抛）。"""
    global _PREDICTOR
    if _PREDICTOR is None:
        try:
            from logic_platform.prediction.serve import Predictor

            _PREDICTOR = Predictor.latest()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("预测模型不可用: %s", exc)
            _PREDICTOR = None
    return _PREDICTOR


def _predict_for(raw: dict) -> dict | None:
    """对 _analyze_raw 产物跑预测（模型缺失/失败 → None）。"""
    predictor = get_predictor()
    if predictor is None:
        return None
    from logic_platform.dsl.interpreter import build_feature_panel
    from logic_platform.prediction.serve import predict_or_none

    df, feats, sig, rec = raw["df"], raw["feats"], raw["sig"], raw["record"]
    panel = build_feature_panel(df, feats, sig, rec)
    return predict_or_none(panel, predictor)


def _analyze_raw(ts_code: str, store) -> dict | None:
    """取数 + signals + 特征 + 状态机，返回组合结果 dict；数据缺失返回 None。"""
    df = store.ohlcv(ts_code, limit=LOOKBACK_BARS)
    if df is None or df.empty or "close" not in df.columns:
        return None

    # 喂 signals：需要 date, open, high, low, close, vol（升序）
    sig_df = df.copy()
    if "vol" not in sig_df.columns:
        sig_df["vol"] = 0.0
    sig_df["vol"] = pd.to_numeric(sig_df["vol"], errors="coerce").fillna(0.0)

    from signals import detect_accumulation_breakout  # 宿主唯一箱体计算

    sig = detect_accumulation_breakout(sig_df)

    # 特征（ohlcv 先、volume 后，链式合并）
    feats = compute_ohlcv_features(df, box=sig)
    feats = compute_volume_features(feats, box=sig)

    # 状态机
    as_of = str(df["date"].iloc[-1])
    rec = StateMachine().evolve(df, sig, as_of=as_of)

    return {"df": df, "feats": feats, "sig": sig, "record": rec}


def analyze(ts_code: str, store) -> dict | None:
    """对外主入口：返回 features API 载荷；数据缺失返回 None。"""
    raw = _analyze_raw(ts_code, store)
    if raw is None:
        return None
    feats, rec = raw["feats"], raw["record"]

    as_of = rec.as_of
    latest = feats.tail(1)
    last_feat = latest.iloc[0].to_dict() if len(latest) else {}

    def _clean(item: dict) -> dict:
        out = {}
        for k, v in item.items():
            if v is None or pd.isna(v):
                continue
            out[k] = round(float(v), 4) if isinstance(v, (int, float)) else v
        return out

    series = feats.tail(SERIES_WINDOW)[
        ["trade_date", "date", "open", "high", "low", "close", "vol",
         "ret_1", "vol_ma_ratio_5_20", "vol_percentile_60", "shrink_days",
         "atr_14", "dist_ma20", "dist_high_60", "vp_corr_20"]
    ]
    series_payload = [_clean(r.to_dict()) for _, r in series.iterrows()]

    return {
        "ts_code": ts_code,
        "as_of": as_of,
        "states": [rec.to_json()],
        "features": {
            "last": _clean(last_feat),
            "series": series_payload,
        },
        "research_only": True,
    }


def explain(ts_code: str, store) -> dict | None:
    """单股 explain：人话解释（docs 附录 A）。"""
    raw = _analyze_raw(ts_code, store)
    if raw is None:
        return None
    feats, rec = raw["feats"], raw["record"]
    as_of = rec.as_of

    last = feats.tail(1)
    vol_f = last.iloc[0].to_dict() if len(last) else {}

    def _f(key):
        v = vol_f.get(key)
        return None if v is None or pd.isna(v) else round(float(v), 4)

    return {
        "ts_code": ts_code,
        "as_of": as_of,
        "state": rec.state,
        "state_since": rec.state_since,
        "box": rec.box,
        "volume": {
            "vol_percentile_60": _f("vol_percentile_60"),
            "vol_ma_ratio_5_20": _f("vol_ma_ratio_5_20"),
            "shrink_days": _f("shrink_days"),
            "breakout_vol_mult": _f("breakout_vol_mult"),
        },
        "prediction": _predict_for(raw),  # Phase 2：模型缺失时为 None
        "reasons": rec.transition_reasons,
        "suggested_dsl_id": _SUGGESTED_DSL.get(rec.state),
        "research_only": True,
        "data_freshness": _freshness(as_of, store),
    }


def predict_batch(ts_codes: list[str], store) -> dict:
    """批量推理（POST /api/logic/predict）。"""
    predictor = get_predictor()
    if predictor is None:
        return {"results": [], "model_version": None,
                "warning": "预测模型未训练（运行 run_logic_train 后可用）",
                "research_only": True}
    from logic_platform.dsl.interpreter import build_feature_panel
    from logic_platform.prediction.serve import predict_or_none

    out = []
    for code in ts_codes:
        raw = _analyze_raw(code, store)
        if raw is None:
            continue
        df, feats, sig, rec = raw["df"], raw["feats"], raw["sig"], raw["record"]
        panel = build_feature_panel(df, feats, sig, rec)
        pred = predict_or_none(panel, predictor)
        if pred is None:
            continue
        out.append({
            "ts_code": code,
            "as_of": rec.as_of,
            "state": rec.state,
            "prediction": pred,
        })
    return {"results": out, "model_version": predictor.version,
            "horizon": predictor.horizon, "research_only": True}


def _freshness(as_of: str, store) -> dict:
    max_date = store.latest_trade_date()  # YYYYMMDD
    ok = max_date is not None and as_of.replace("-", "") == max_date
    return {"ok": ok, "max_trade_date": max_date, "as_of": as_of}
