"""模型测试：三种模型训练/推理（合成数据）。"""
from __future__ import annotations

import numpy as np
import pytest

from logic_platform.prediction.models import (
    HistGBModel,
    LogisticBaseline,
    StateStatsTable,
    train_model,
)


def _synthetic(n=800, seed=1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5))
    # y 与第一列正相关 → 可学习
    p = 1 / (1 + np.exp(-X[:, 0] * 2))
    y = (rng.random(n) < p).astype(float)
    return X, y


def test_logistic_train_predict():
    X, y = _synthetic()
    m = LogisticBaseline().train(X, y)
    proba = m.predict_proba(X)
    assert proba.shape == (len(X),)
    assert ((proba >= 0) & (proba <= 1)).all()
    assert m.fitted


def test_histgb_train_predict():
    X, y = _synthetic()
    m = HistGBModel().train(X, y)
    proba = m.predict_proba(X)
    assert proba.shape == (len(X),)
    assert ((proba >= 0) & (proba <= 1)).all()


def test_stats_table_train_predict():
    X, y = _synthetic()
    states = (np.arange(len(y)) % 3).astype(int)  # 0/1/2
    vol = np.random.default_rng(2).random(len(y))
    m = StateStatsTable().train(X, y, states=states, vol=vol)
    out = m.predict_proba(X, states=states, vol=vol)
    assert out.shape == (len(y),)
    assert ((out >= 0) & (out <= 1)).all()
    # 未知状态 → fallback（不抛）
    out2 = m.predict_proba(X, states=np.full(len(y), 9), vol=vol)
    assert out2.shape == (len(y),)


def test_train_model_factory():
    X, y = _synthetic(200)
    for kind in ("logistic", "histgb", "stats"):
        m = train_model(X, y, kind=kind, states=np.zeros(len(y)),
                        vol=np.zeros(len(y)))
        assert m.fitted


def test_train_model_bad_kind():
    with pytest.raises(ValueError):
        train_model(np.zeros((10, 2)), np.zeros(10), kind="nope")


def test_single_class_raises():
    X, y = _synthetic(100)
    with pytest.raises(ValueError):
        LogisticBaseline().train(X, np.ones(len(y)))
