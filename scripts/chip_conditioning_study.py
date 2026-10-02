"""Phase 2-A：突破形态 × 筹码结构（获利盘/成本分散）条件研究。

预登记：docs/PREREGISTRATION-CHIP-OVERHANG-2026-09-15.md
（先冻结假设/特征/分位/阈值，再看结果；结果只允许 PASS_CANDIDATE / FAIL /
INSUFFICIENT 三种，不生成候选、不改每日参数。）

输入复用 Phase 1 匹配对照产物：
  runtime/research/event-study-*/diffs.json
每个事件已有 event/control/diff（次日开盘入场，+H 收盘出场）。
本脚本把事件日筹码特征并进来，按分位看条件收益差，回答：
  「低获利盘 / 低成本分散的突破，条件收益是否更好？」

用法：
  .venv312\\Scripts\\python.exe -X utf8 scripts/chip_conditioning_study.py \\
      --diffs runtime/research/event-study-v1-full/diffs.json \\
      --db runtime/stock_data.db --out runtime/research/H-20260915-chip-overhang
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ab_screener.research.event_study import block_bootstrap_ci

_TZ = ZoneInfo("Asia/Shanghai")
PRIMARY_HORIZON = 20
SECONDARY_HORIZONS = (5, 10, 60)
FEATURES = {
    "winner_rate": "获利盘比例（筹码获利占比，%）",
    "dispersion": "成本分散度 (cost_95pct-cost_5pct)/weight_avg",
}
ALPHA = 0.025  # 两个主检验的 Bonferroni 校正（单侧）


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_events(diffs_path: Path) -> list[dict]:
    payload = json.loads(diffs_path.read_text(encoding="utf-8"))
    rows = payload["event"][str(PRIMARY_HORIZON)]
    out = []
    for r in rows:
        out.append({"ts_code": r["ts_code"], "signal_date": r["date"], "diff20": r["diff"]})
    return out


def _load_chips(db: Path, events: list[dict], *, strict_pit: bool) -> pd.DataFrame:
    ev = pd.DataFrame(events)
    dmin, dmax = str(ev["signal_date"].min()), str(ev["signal_date"].max())
    codes = sorted(ev["ts_code"].unique().tolist())
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA query_only=1")
    try:
        frames = []
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            marks = ",".join("?" * len(chunk))
            frames.append(pd.read_sql_query(
                f"SELECT ts_code, trade_date, available_at, payload_json FROM cyq_history "
                f"WHERE ts_code IN ({marks}) AND trade_date>=? AND trade_date<=?",
                con,
                params=[*chunk, dmin, dmax],
            ))
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        cal = [str(r[0]) for r in con.execute(
            "SELECT cal_date FROM trade_cal WHERE is_open=1 ORDER BY cal_date"
        ).fetchall()]
    finally:
        con.close()
    if raw.empty:
        return pd.DataFrame()
    # 只保留事件 (code, signal_date) 对，避免解析 100 万级 payload
    pairs = ev[["ts_code", "signal_date"]].drop_duplicates()
    raw = raw.merge(pairs, left_on=["ts_code", "trade_date"], right_on=["ts_code", "signal_date"], how="inner")
    if raw.empty:
        return pd.DataFrame()
    parsed = pd.json_normalize(raw["payload_json"].map(json.loads))
    frame = pd.concat([raw.drop(columns=["payload_json"]).reset_index(drop=True), parsed], axis=1)
    frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
    cal_index = {d: i for i, d in enumerate(cal)}

    def entry_dt(signal_date: str):
        i = cal_index.get(signal_date)
        if i is None or i + 1 >= len(cal):
            return pd.NaT
        return pd.Timestamp(f"{cal[i + 1]}T09:15:00", tz=_TZ)

    frame["_entry_dt"] = frame["trade_date"].map(entry_dt)
    frame = frame[frame["_entry_dt"].notna()]
    if strict_pit:
        # 预登记口径：筹码数据必须在下一次开盘前已经可用
        frame = frame[frame["available_at"].notna()]
        frame = frame[frame["available_at"] <= frame["_entry_dt"]]
    if "weight_avg" in frame.columns:
        frame["dispersion"] = (frame["cost_95pct"] - frame["cost_5pct"]) / frame["weight_avg"]
    return frame


def _merge(events: list[dict], chips: pd.DataFrame) -> pd.DataFrame:
    ev = pd.DataFrame(events)
    use = ["ts_code", "trade_date", "winner_rate", "dispersion"]
    merged = ev.merge(chips[use], left_on=["ts_code", "signal_date"], right_on=["ts_code", "trade_date"], how="inner")
    return merged.dropna(subset=["diff20", "winner_rate", "dispersion"])


def _quintile_table(frame: pd.DataFrame, feature: str) -> pd.DataFrame:
    frame = frame.copy()
    frame["q"] = pd.qcut(frame[feature], 5, labels=False, duplicates="drop")
    rows = []
    for q, group in frame.groupby("q", sort=True):
        ci = block_bootstrap_ci(group["signal_date"], group["diff20"], reps=2000, seed=20260915)
        rows.append({
            "q": int(q) + 1,
            "n": len(group),
            "feature_mean": float(group[feature].mean()),
            "diff_mean": ci["mean"],
            "diff_lo95": ci["lo"],
            "diff_hi95": ci["hi"],
            "positive_rate": ci["positive_rate"],
        })
    return pd.DataFrame(rows)


def _low_minus_high(frame: pd.DataFrame, feature: str) -> dict:
    q = pd.qcut(frame[feature], 5, labels=False, duplicates="drop")
    low = frame.loc[q == q.min()]
    high = frame.loc[q == q.max()]
    dates = sorted(set(frame["signal_date"]))
    low_sum = low.groupby("signal_date")["diff20"].sum().reindex(dates, fill_value=0.0).to_numpy()
    low_cnt = low.groupby("signal_date")["diff20"].count().reindex(dates, fill_value=0).to_numpy()
    high_sum = high.groupby("signal_date")["diff20"].sum().reindex(dates, fill_value=0.0).to_numpy()
    high_cnt = high.groupby("signal_date")["diff20"].count().reindex(dates, fill_value=0).to_numpy()
    stat = float(low["diff20"].mean() - high["diff20"].mean())
    rng = np.random.default_rng(20260915)
    stats = np.empty(2000)
    for b in range(2000):
        pick = rng.integers(0, len(dates), len(dates))
        lc, hc = int(low_cnt[pick].sum()), int(high_cnt[pick].sum())
        lo = float(low_sum[pick].sum() / lc) if lc else np.nan
        hi = float(high_sum[pick].sum() / hc) if hc else np.nan
        stats[b] = lo - hi
    with np.errstate(invalid="ignore"):
        lower = float(np.nanpercentile(stats, 100 * ALPHA))
    return {
        "n_low": int(len(low)), "n_high": int(len(high)),
        "low_mean": float(low["diff20"].mean()), "high_mean": float(high["diff20"].mean()),
        "low_minus_high": stat, "ci_lower_one_sided": lower,
    }


def _spearman(frame: pd.DataFrame, feature: str) -> float:
    q = pd.qcut(frame[feature], 5, labels=False, duplicates="drop")
    means = frame.assign(q=q).groupby("q")["diff20"].mean()
    if len(means) < 3:
        return float("nan")
    return float(pd.Series(means.index).corr(pd.Series(means.to_numpy()), method="spearman"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--diffs", type=Path, required=True)
    p.add_argument("--db", type=Path, default=_ROOT / "runtime" / "stock_data.db")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--allow-unverified-pit", action="store_true",
                   help="预登记 PIT 口径样本不足时，允许运行不可晋级的探索模式")
    args = p.parse_args()
    if args.out.exists():
        raise SystemExit(f"输出目录已存在，拒绝覆盖: {args.out}")
    args.out.mkdir(parents=True, exist_ok=False)

    events = _load_events(args.diffs)
    frame = _merge(events, _load_chips(args.db, events, strict_pit=True))
    strict_n = int(len(frame))
    pit_mode = "STRICT_PIT"
    verdict_prefix = ""
    if strict_n < 300:
        _write_json(args.out / "result_strict_pit.json", {
            "study": "H-20260915-chip-overhang",
            "check": "strict_pit_sample",
            "verdict": "INSUFFICIENT_PIT",
            "n_events_total": len(events),
            "n_events_with_strict_pit_chips": strict_n,
            "reason": "cyq_history.available_at 为批量入库时间；按预登记口径几乎没有事件能在次日开盘前看到筹码值",
            "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        })
        print(f"[chip] strict PIT 可用事件={strict_n} < 300 → INSUFFICIENT_PIT")
        if not args.allow_unverified_pit:
            (args.out / "report.md").write_text(
                "# Phase 2-A：突破 × 筹码结构\n\n"
                "- 裁决：**INSUFFICIENT_PIT**\n"
                f"- 全部事件 {len(events)}；满足 `available_at ≤ 次日开盘` 的仅 {strict_n} 个\n"
                "- 筹码历史为 2026-08 批量入库，`available_at` 是入库时间，不能当作历史决策时点可用。\n"
                "- 不放开该门禁、不产生任何条件收益结论；若需探索请显式加 `--allow-unverified-pit`，结果不可晋级。\n",
                encoding="utf-8",
            )
            return 2
        frame = _merge(events, _load_chips(args.db, events, strict_pit=False))
        pit_mode = "UNVERIFIED_CONTEMPORANEOUS"
        verdict_prefix = "EXPLORATORY_"
        print(f"[chip] 探索模式（不可晋级）：事件={len(frame)}")
    if frame.empty:
        raise SystemExit("筹码数据为空")
    print(f"[chip] events={len(events)} with_pit_chips={len(frame)} "
          f"dates={frame['signal_date'].min()}..{frame['signal_date'].max()}")

    features_result: dict[str, dict] = {}
    for feature, description in FEATURES.items():
        table = _quintile_table(frame, feature)
        test = _low_minus_high(frame, feature)
        rho = _spearman(frame, feature)
        passed = (test["low_minus_high"] > 0) and (test["ci_lower_one_sided"] > 0) and (rho < 0)
        features_result[feature] = {
            "description": description,
            "quintiles": table.to_dict(orient="records"),
            "low_minus_high_H20": test,
            "spearman_quintile_mean_gradient": rho,
            "pass": bool(passed),
        }

    wins = sum(1 for f in features_result.values() if f["pass"])
    if len(frame) < 300:
        verdict = "INSUFFICIENT"
    elif wins == len(FEATURES):
        verdict = "PASS_CANDIDATE"
    elif all(f["low_minus_high_H20"]["low_minus_high"] <= 0 for f in features_result.values()):
        verdict = "FAIL"
    else:
        verdict = "INSUFFICIENT"
    verdict = verdict_prefix + verdict

    payload = {
        "study": "H-20260915-chip-overhang",
        "version": "v1",
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "diffs_source": str(args.diffs),
        "diffs_sha256": _sha256_file(args.diffs),
        "script_sha256": _sha256_file(Path(__file__).resolve()),
        "preregistration": "docs/PREREGISTRATION-CHIP-OVERHANG-2026-09-15.md",
        "pit_mode": pit_mode,
        "candidate_eligible": pit_mode == "STRICT_PIT",
        "strict_pit_events": strict_n,
        "primary_horizon": PRIMARY_HORIZON,
        "alpha_one_sided": ALPHA,
        "n_events_with_chips": int(len(frame)),
        "n_dates": int(frame["signal_date"].nunique()),
        "verdict": verdict,
        "features": features_result,
    }
    (args.out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Phase 2-A：突破 × 筹码结构（H-20260915-chip-overhang）",
        "",
        f"- 裁决：**{verdict}**",
        f"- PIT 模式：`{pit_mode}`；可晋级：{pit_mode == 'STRICT_PIT'}",
        f"- 事件（有筹码）：{len(frame)} 个，{frame['signal_date'].nunique()} 个交易日，{frame['signal_date'].min()}..{frame['signal_date'].max()}；预登记严格 PIT 可用 {strict_n} 个",
        f"- 主要 horizon：H={PRIMARY_HORIZON}；单侧 α={ALPHA}（两主检验 Bonferroni）",
        "- 差异口径：事件 − Phase 1 匹配对照（次日开盘入场，+H 收盘出场，未计费用）",
        "",
    ]
    for feature, res in features_result.items():
        lines += [
            f"## {feature}",
            "",
            f"{res['description']}；五分组条件收益差与低−高检验：",
            "",
            "| 分位 | n | 特征均值 | 条件收益差均值 | 95% CI | 为正占比 |",
            "|---|---:|---:|---:|---|---:|",
        ]
        for row in res["quintiles"]:
            lines.append(
                f"| Q{row['q']} | {row['n']} | {row['feature_mean']:.3f} | {row['diff_mean']*100:+.2f}% | "
                f"[{row['diff_lo95']*100:+.2f}%, {row['diff_hi95']*100:+.2f}%] | {row['positive_rate']*100:.1f}% |"
            )
        t = res["low_minus_high_H20"]
        flag = "方向符合" if res["pass"] else "方向不符"
        lines += [
            "",
            f"- Q1−Q5（H20）：{t['low_minus_high']*100:+.2f}%（单侧下限 {t['ci_lower_one_sided']*100:+.2f}%），"
            f"分位梯度 Spearman ρ={res['spearman_quintile_mean_gradient']:.2f} → {flag}",
            "",
        ]
    lines += [
        "## 边界",
        "",
        "- 复用 Phase 1 同一匹配对照；筹码数据 2022-08 起，早期事件自动剔除。",
        "- 未计费用/滑点/涨跌停不可成交性；本结果只回答条件异质性，不构成策略收益。",
        "- 两个特征之外不再新增特征；PASS 也只进入 shadow，不自动改每日参数。",
    ]
    if pit_mode != "STRICT_PIT":
        lines += [
            "- **本报告为探索模式**：`cyq_history.available_at` 是批量入库时间，历史事件无法按预登记 PIT 口径使用；",
            "  探索结果只用于判断后续是否值得建设真 PIT 筹码数据，不构成候选、不进入任何晋级链。",
        ]
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[chip] verdict={verdict}")
    print(f"[chip] artifact={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
