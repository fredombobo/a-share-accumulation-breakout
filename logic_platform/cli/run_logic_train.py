"""Phase 2 训练 CLI：特征面板 → 标签 → walk-forward → 模型落盘。

用法：
  C:\\Python314\\python.exe -m logic_platform.cli.run_logic_train \\
      --codes 200 --horizon 10 --model histgb --start 20230101 --end 20260731
      --out runtime/logic_models/v1

输出（out 目录）：
  - model.joblib  （统一接口模型）
  - meta.json     （model_version/features/horizon/train_window/metrics/expected_ret_by_state）

预期耗时：200 只 × 每 5 交易日状态评估 ≈ 2~4 分钟（单进程）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from logic_platform.data.ab_store import ABStore
from logic_platform.prediction.dataset import (
    FEATURE_COLS,
    STATE_COLS,
    build_panel,
    to_matrix,
    train_test_split_timewise,
)

_ROOT = Path(__file__).resolve().parents[2]
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("logic.train")


def _eval(X, y, model, label: str) -> dict:
    """IS/OOS 评估：AUC + 准确率 + 桶内胜率。"""
    from sklearn.metrics import accuracy_score, roc_auc_score

    proba = model.predict_proba(X)
    out = {"n": int(len(y)), "auc": None, "accuracy": None, "win_rate": float(y.mean())}
    if len(np_unique(y)) > 1:
        try:
            out["auc"] = round(float(roc_auc_score(y, proba)), 4)
        except ValueError:
            out["auc"] = None
        out["accuracy"] = round(float(accuracy_score(y, (proba >= 0.5).astype(int))), 4)
    # 分桶胜率：proba 高分组（top 30%）的真实胜率
    if len(proba) >= 30:
        top = np_argsort(proba)[-max(1, int(len(proba) * 0.3)):]
        out["top30_win_rate"] = round(float(y[top].mean()), 4)
    return out


def np_unique(y):
    import numpy as np

    return np.unique(y)


def np_argsort(a):
    import numpy as np

    return np.argsort(a)


def main() -> int:
    ap = argparse.ArgumentParser(description="训练预测模型（docs §5）")
    ap.add_argument("--codes", type=int, default=200, help="股票数上限")
    ap.add_argument("--horizon", type=int, default=10, choices=[5, 10, 20])
    ap.add_argument("--model", default="histgb", choices=["logistic", "histgb", "stats"])
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--out", default=None, help="输出目录（默认 runtime/logic_models/v{N}）")
    args = ap.parse_args()

    t0 = time.time()
    log.info("══ Phase 2 训练开始 ══ horizon=%d model=%s", args.horizon, args.model)

    store = ABStore()
    codes = store.universe_from_stock_basic()[: args.codes]
    log.info("构建面板: %d 只 %s ~ %s", len(codes), args.start, args.end)

    panel = build_panel(store, codes, args.start, args.end,
                        horizon=args.horizon)
    if panel.empty:
        log.error("面板为空——检查数据/区间")
        return 1
    log.info("面板: %d 样本（%d 只，状态聚焦）", len(panel),
             panel["ts_code"].nunique())
    log.info("状态分布: %s",
             {k: int(v) for k, v in panel["state"].value_counts().items()})

    is_df, oos_df = train_test_split_timewise(panel, test_ratio=0.3,
                                              horizon=args.horizon)
    log.info("切分: IS %d（%s 前） / OOS %d（%s 起）",
             len(is_df), is_df["trade_date"].iloc[-1],
             len(oos_df), oos_df["trade_date"].iloc[0])

    X_is, y_up, _y_ret, _y_mdd = to_matrix(is_df, args.horizon)
    X_oos, y_oos_up, _y_oos_ret, _ = to_matrix(oos_df, args.horizon)

    from logic_platform.prediction.models import train_model

    model = train_model(
        X_is, y_up, kind=args.model,
        states=is_df["state"].values,
        vol=is_df["vol_percentile_60"].fillna(0.0).values,
    )
    log.info("训练完成: %s", model.name)

    is_eval = _eval(X_is, y_up, model, "IS")
    oos_eval = _eval(X_oos, y_oos_up, model, "OOS")
    log.info("IS  %s", is_eval)
    log.info("OOS %s", oos_eval)

    # 期望收益表（按 state，训练集 y_ret 均值）
    ret_by_state: dict[str, dict] = {}
    for st, grp in is_df.groupby("state"):
        ret_by_state[str(st)] = {
            "avg_ret": round(float(grp[f"y_ret_{args.horizon}"].mean()), 5),
            "win_rate": round(float(grp[f"y_up_{args.horizon}"].mean()), 5),
            "n": int(len(grp)),
        }

    # 落盘
    out_dir = Path(args.out) if args.out else None
    if out_dir is None:
        idx = 1
        while (Path(__file__).resolve().parents[2] / "runtime" / "logic_models" / f"v{idx}").exists():
            idx += 1
        out_dir = Path(__file__).resolve().parents[2] / "runtime" / "logic_models" / f"v{idx}"
    out_dir.mkdir(parents=True, exist_ok=True)

    import joblib

    joblib.dump(model, out_dir / "model.joblib")
    meta = {
        "model_version": out_dir.name,
        "model_type": args.model,
        "horizon": args.horizon,
        "features": FEATURE_COLS + STATE_COLS,
        "train_window": {"start": args.start, "end": args.end,
                         "is_end": is_df["trade_date"].iloc[-1],
                         "oos_start": oos_df["trade_date"].iloc[0],
                         "oos_end": oos_df["trade_date"].iloc[-1]},
        "metrics": {"is": is_eval, "oos": oos_eval},
        "expected_ret_by_state": ret_by_state,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_samples": {"is": int(len(is_df)), "oos": int(len(oos_df))},
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("模型落盘: %s（%.1fs）", out_dir, time.time() - t0)

    print("\n════ 训练结果 ════")
    print(f"版本     : {out_dir.name}（{args.model}，horizon={args.horizon}）")
    print(f"样本     : IS {len(is_df)} / OOS {len(oos_df)}（{panel['ts_code'].nunique()} 只）")
    print(f"OOS AUC  : {oos_eval.get('auc')}   准确率 {oos_eval.get('accuracy')}")
    print(f"OOS 胜率 : {oos_eval.get('win_rate')}   Top30% 胜率 {oos_eval.get('top30_win_rate')}")
    print(f"期望收益 : {ret_by_state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
