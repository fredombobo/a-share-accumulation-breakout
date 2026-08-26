"""预测模型（docs §5.2）：统一接口，baseline 先行。

- LogisticBaseline：逻辑回归（标准 baseline，sklearn）
- HistGBModel：HistGradientBoosting（sklearn 原生梯度提升，性能接近 LightGBM；
  未来可无缝替换 lightgbm——接口不变）
- StateStatsTable：状态 × 量能分位条件概率表（最可解释，serve 直查）

统一接口：train(X, y, ...) → fit；predict_proba(X) → p_up（float array）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

_LOGGER = logging.getLogger(__name__)


class BaseModel:
    """统一接口基类。"""

    def train(self, X: np.ndarray, y: np.ndarray) -> BaseModel:
        raise NotImplementedError

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """返回 P(up) ∈ [0,1]，shape=(n,)。"""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


class LogisticBaseline(BaseModel):
    def __init__(self, C: float = 1.0, max_iter: int = 1000):
        from sklearn.linear_model import LogisticRegression

        self._clf = LogisticRegression(C=C, max_iter=max_iter)
        self.fitted = False

    def train(self, X, y):

        if len(np.unique(y)) < 2:
            raise ValueError("标签只有单一类别，无法训练分类器")
        self._clf.fit(X, y)
        self.fitted = True
        return self

    def predict_proba(self, X):
        if not self.fitted:
            raise RuntimeError("模型未训练")
        return self._clf.predict_proba(X)[:, 1]


class HistGBModel(BaseModel):
    def __init__(self, max_iter: int = 300, learning_rate: float = 0.05,
                 max_depth: int | None = 4, min_samples_leaf: int = 50):
        from sklearn.ensemble import HistGradientBoostingClassifier

        self._clf = HistGradientBoostingClassifier(
            max_iter=max_iter, learning_rate=learning_rate,
            max_depth=max_depth, min_samples_leaf=min_samples_leaf,
        )
        self.fitted = False

    def train(self, X, y):
        if len(np.unique(y)) < 2:
            raise ValueError("标签只有单一类别，无法训练分类器")
        self._clf.fit(X, y)
        self.fitted = True
        return self

    def predict_proba(self, X):
        if not self.fitted:
            raise RuntimeError("模型未训练")
        return self._clf.predict_proba(X)[:, 1]


@dataclass
class StateStatsTable(BaseModel):
    """状态 × 量能分位 条件概率/期望收益表（可解释 baseline）。

    训练：按 (state, vol_bucket) 分箱统计历史胜率与平均收益。
    推理：查表，未知分箱回退到 state 全局均值。
    """

    vol_bins: tuple[float, ...] = (0.0, 0.4, 0.7, 1.01)
    table: dict = None  # type: ignore[assignment]
    fallback: dict = None  # type: ignore[assignment]
    fitted: bool = False

    def _bucket(self, v: float) -> int:
        for i in range(len(self.vol_bins) - 1):
            if self.vol_bins[i] <= v < self.vol_bins[i + 1]:
                return i
        return len(self.vol_bins) - 2

    def train(self, X, y, states: np.ndarray | None = None, vol: np.ndarray | None = None):
        """需额外传 states 标签与量能分位列（构造子类或直接传）：
        若未传，退化为全样本单箱（等价全局均值）。"""
        n = len(y)
        if states is None:
            states = np.zeros(n, dtype=int)
        if vol is None:
            vol = np.zeros(n, dtype=float)
        tbl: dict[str, dict] = {}
        fallback: dict[str, dict] = {}
        for st in np.unique(states):
            m = states == st
            fb = {"win_rate": float(np.mean(y[m])), "avg_ret": 0.0}
            fallback[str(st)] = fb
            for b in range(len(self.vol_bins) - 1):
                sel = m & (vol >= self.vol_bins[b]) & (vol < self.vol_bins[b + 1])
                if sel.sum() >= 10:
                    tbl[f"{st}|{b}"] = {"win_rate": float(np.mean(y[sel])),
                                        "n": int(sel.sum())}
        self.table = tbl
        self.fallback = fallback
        self.fitted = True
        return self

    def predict_proba(self, X, states=None, vol=None):
        if not self.fitted:
            raise RuntimeError("模型未训练")
        if states is None or vol is None:
            raise ValueError("StateStatsTable 推理需要 states 与 vol 列")
        out: np.ndarray = np.empty(len(X), dtype=float)
        for i in range(len(X)):
            key = f"{states[i]}|{self._bucket(float(vol[i]))}"
            out[i] = self.table.get(key, self.fallback.get(str(states[i]), {})).get(
                "win_rate", 0.5)
        return out


def train_model(
    X: np.ndarray, y: np.ndarray,
    kind: str = "histgb",
    states: np.ndarray | None = None,
    vol: np.ndarray | None = None,
) -> BaseModel:
    """工厂：kind ∈ {logistic, histgb, stats}。"""
    if kind == "logistic":
        return LogisticBaseline().train(X, y)
    if kind == "histgb":
        return HistGBModel().train(X, y)
    if kind == "stats":
        return StateStatsTable().train(X, y, states=states, vol=vol)
    raise ValueError(f"未知模型类型: {kind}（可用: logistic|histgb|stats）")
