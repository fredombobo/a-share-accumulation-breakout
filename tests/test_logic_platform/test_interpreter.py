"""解释器测试：条件求值、ref、NaN、pred 降级、all/any 逻辑。"""
from __future__ import annotations

from logic_platform.dsl.interpreter import Interpreter, build_feature_panel
from logic_platform.dsl.parser import parse_text
from logic_platform.dsl.schema import Condition

DSL = """
strategy:
  id: t
  name: t
entry:
  all:
    - { feature: "structure.state", op: "in", value: ["BREAKOUT"] }
    - { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }
  any: []
"""


def _interp():
    return Interpreter()


def _panel(**kw):
    p = {
        "structure.state": "BREAKOUT",
        "structure.is_breakout": True,
        "structure.box_high": 10.5, "structure.box_low": 9.5, "structure.box_mid": 10.0,
        "vol_ma_ratio_5_20": 1.8, "vol_percentile_60": 0.9, "shrink_days": 0,
        "breakout_vol_mult": 1.7, "close": 10.6, "vol": 2e7,
        "ma5": 10.3, "ma10": 10.1, "ma20": 9.9,
        "ret_1": 0.03, "ret_5": 0.05, "atr_14": 0.2, "dist_ma20": 0.07,
        "pred.p_up_10": None,
    }
    p.update(kw)
    return p


def test_eval_ops():
    i = _interp()
    p = _panel()
    assert i.eval_condition(Condition(feature="close", op=">=", value=10.5), p, []) is True
    assert i.eval_condition(Condition(feature="close", op="<", value=10.5), p, []) is False
    assert i.eval_condition(Condition(feature="close", op="==", value=10.6), p, []) is True
    assert i.eval_condition(Condition(feature="close", op="!=", value=1), p, []) is True
    assert i.eval_condition(Condition(feature="structure.state", op="in",
                                      value=["BREAKOUT", "FAIL"]), p, []) is True
    assert i.eval_condition(Condition(feature="structure.state", op="not_in",
                                      value=["IDLE"]), p, []) is True


def test_eval_ref():
    i = _interp()
    p = _panel()
    c = Condition(feature="close", op=">=", ref="box_mid")
    assert i.eval_condition(c, p, []) is True  # 10.6 >= 10.0


def test_eval_is_nan():
    i = _interp()
    p = _panel(close=None)
    assert i.eval_condition(Condition(feature="close", op="is_nan"), p, []) is True
    assert i.eval_condition(Condition(feature="close", op="not_nan"), p, []) is False


def test_pred_degrades_to_false_with_warning():
    i = _interp()
    p = _panel()
    w = []
    ok = i.eval_condition(Condition(feature="pred.p_up_10", op=">=", value=0.55), p, w)
    assert ok is False
    assert any("pred.p_up_10" in x for x in w)


def test_evaluate_all_hit():
    i = _interp()
    dsl = parse_text(DSL)
    r = i.evaluate(dsl, _panel())
    assert r.passed is True
    assert len(r.reasons) == 2


def test_evaluate_one_miss():
    i = _interp()
    dsl = parse_text(DSL)
    r = i.evaluate(dsl, _panel(vol_ma_ratio_5_20=1.2))
    assert r.passed is False
    assert r.reasons == []


def test_evaluate_any_group():
    # any 组：all 组只剩 state 条件（量比条件移除，避免 all 组先行拦截）
    dsl = parse_text(DSL.replace(
        '    - { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }\n',
        "").replace("  any: []", """
  any:
    - { feature: "vol_percentile_60", op: ">=", value: 0.95 }
"""))
    i = _interp()
    assert i.evaluate(dsl, _panel(vol_percentile_60=0.97)).passed is True
    assert i.evaluate(dsl, _panel(vol_percentile_60=0.3)).passed is False


def test_build_panel_from_raw(store):
    """end-to-end：隔离行情的 _analyze_raw 产物 → 面板 → 求值。"""
    from logic_platform.service import _analyze_raw

    raw = _analyze_raw("000001.SZ", store)
    assert raw is not None
    df, feats, sig, rec = raw["df"], raw["feats"], raw["sig"], raw["record"]
    panel = build_feature_panel(df, feats, sig, rec)
    assert "structure.state" in panel
    assert panel["structure.state"] == rec.state
    assert "close" in panel and panel["close"] > 0
    assert "pred.p_up_10" in panel and panel["pred.p_up_10"] is None
    # 面板值可被解释器求值（不抛异常）
    i = _interp()
    for cond in (
        Condition(feature="structure.state", op="in", value=["BREAKOUT", "FAIL"]),
        Condition(feature="vol_percentile_60", op=">=", value=0.0),
    ):
        i.eval_condition(cond, panel, [])
