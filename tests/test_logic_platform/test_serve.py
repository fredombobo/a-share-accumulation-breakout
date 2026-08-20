"""Predictor 推理服务测试：加载、输出契约、降级。"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pytest

from logic_platform.prediction.models import LogisticBaseline
from logic_platform.prediction.serve import Predictor, predict_or_none

FEATURES = ["vol_percentile_60", "vol_ma_ratio_5_20", "state_BREAKOUT"]


@pytest.fixture()
def model_dir(tmp_path):
    """模型目录：{tmp}/v1/model.joblib + meta.json。"""
    d = tmp_path / "v1"
    d.mkdir()
    X = np.random.default_rng(0).normal(size=(200, len(FEATURES)))
    y = (X[:, 0] > 0).astype(float)
    m = LogisticBaseline().train(X, y)
    joblib.dump(m, d / "model.joblib")
    (d / "meta.json").write_text(json.dumps({
        "model_version": "v1", "model_type": "logistic", "horizon": 10,
        "features": FEATURES,
        "train_window": {"start": "20230101", "end": "20260731"},
        "expected_ret_by_state": {
            "BREAKOUT": {"avg_ret": 0.03, "win_rate": 0.55, "n": 100},
            "ACCUMULATION": {"avg_ret": -0.01, "win_rate": 0.45, "n": 80},
        },
    }), encoding="utf-8")
    return d


def _panel(**kw):
    p = {"structure.state": "BREAKOUT", "vol_percentile_60": 0.8,
         "vol_ma_ratio_5_20": 1.9, "state_BREAKOUT": 1.0}
    p.update(kw)
    return p


def test_predict_contract(model_dir):
    p = Predictor("v1", model_dir.parent)
    out = p.predict(_panel())
    for key in ["p_up", "expected_ret", "fail_risk", "model_version",
                "train_window", "horizon"]:
        assert key in out, f"缺字段 {key}"
    assert 0 <= out["p_up"] <= 1
    assert out["fail_risk"] == pytest.approx(1 - out["p_up"], abs=1e-4)
    assert out["model_version"] == "v1"
    assert out["expected_ret"] == pytest.approx(0.03)


def test_predict_missing_feature_uses_zero(model_dir):
    p = Predictor("v1", model_dir.parent)
    out = p.predict({"structure.state": "BREAKOUT"})  # 全缺 → 0
    assert out is not None and 0 <= out["p_up"] <= 1


def test_latest_picks_newest(model_dir, tmp_path):
    """v1 与 v2 并存 → latest 选 v2（语义版本比较）。"""
    import shutil

    shutil.copytree(model_dir, tmp_path / "v2")
    (tmp_path / "v2" / "meta.json").write_text(json.dumps({
        "model_version": "v2", "features": FEATURES, "horizon": 10,
        "expected_ret_by_state": {},
    }), encoding="utf-8")
    p = Predictor.latest(tmp_path)
    assert p is not None
    assert p.version == "v2"


def test_latest_none_when_missing(tmp_path):
    assert Predictor.latest(tmp_path / "nope") is None


def test_predict_or_none_guards(model_dir):
    assert predict_or_none(_panel(), None) is None
    assert predict_or_none({}, Predictor("v1", model_dir.parent)) is not None


def test_missing_model_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Predictor("v99", tmp_path)
