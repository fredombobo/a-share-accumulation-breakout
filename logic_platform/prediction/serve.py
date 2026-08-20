"""预测推理服务（docs §5.2 输出契约）。

加载 runtime/logic_models/{version}/（model.joblib + meta.json），
输入特征面板 → {p_up, expected_ret, fail_risk, model_version, train_window}。

- expected_ret：训练时按 state 统计的均值收益表（meta.expected_ret_by_state）
- fail_risk：1 - p_up（MVP 口径，文档附录 A 字段）
- 模型缺失 → predict 返回 None（调用方降级，不崩）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

_LOGGER = logging.getLogger(__name__)

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[2] / "runtime" / "logic_models"


class Predictor:
    """单版本模型加载器 + 推理。"""

    def __init__(self, version: str, model_dir: Path | None = None):
        self.version = version
        self.model_dir = Path(model_dir or DEFAULT_MODELS_DIR) / version
        if not self.model_dir.exists():
            raise FileNotFoundError(f"模型不存在: {self.model_dir}")
        meta_path = self.model_dir / "meta.json"
        with open(meta_path, encoding="utf-8") as fh:
            self.meta = json.load(fh)

        import joblib

        self.model = joblib.load(self.model_dir / "model.joblib")
        self.features = self.meta.get("features", [])
        self.horizon = int(self.meta.get("horizon", 10))
        self._ret_by_state = self.meta.get("expected_ret_by_state", {})

    @classmethod
    def latest(cls, model_dir: Path | None = None) -> Predictor | None:
        """加载最新版本；无模型返回 None。"""
        base = Path(model_dir or DEFAULT_MODELS_DIR)
        if not base.exists():
            return None
        versions = sorted(
            (p.name for p in base.iterdir() if (p / "meta.json").exists()),
            key=lambda v: [int(x) for x in v.lstrip("v").split(".") if x.isdigit()] or [0],
        )
        if not versions:
            return None
        return cls(versions[-1], base)

    # ── 推理 ──

    def _to_vector(self, panel: dict) -> np.ndarray | None:
        """特征面板 → 模型输入向量（meta.features 顺序）；缺失列填 0。"""
        if not self.features:
            return None
        row = []
        for col in self.features:
            v = panel.get(col)
            row.append(0.0 if v is None or (isinstance(v, float) and np.isnan(v)) else float(v))
        return np.asarray(row, dtype=np.float32).reshape(1, -1)

    def predict(self, panel: dict) -> dict | None:
        """面板 → 预测；关键特征缺失返回 None。"""
        X = self._to_vector(panel)
        if X is None:
            return None
        try:
            proba = self.model.predict_proba(X)[0]
        except Exception as exc:  # noqa: BLE001 —— StateStatsTable 需要 states/vol 参数
            _LOGGER.debug("predict_proba 失败（%s），回退统计表口径", exc)
            st = panel.get("structure.state")
            p = self._ret_by_state.get(str(st), {}).get("win_rate", 0.5)
            proba = float(p)
        p_up = round(float(proba), 4)
        st = panel.get("structure.state")
        er = self._ret_by_state.get(str(st), {}).get("avg_ret")
        return {
            "p_up": p_up,
            "expected_ret": round(float(er), 4) if er is not None else None,
            "fail_risk": round(1.0 - p_up, 4),
            "model_version": self.version,
            "train_window": self.meta.get("train_window"),
            "horizon": self.horizon,
        }


def predict_or_none(panel: dict, predictor: Predictor | None) -> dict | None:
    """带 None 防护的推理（模型缺失/失败 → None，调用方降级）。"""
    if predictor is None:
        return None
    try:
        return predictor.predict(panel)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("预测失败: %s", exc)
        return None
