"""Phase 1：A 池 strict 形态的匹配对照事件研究（先证伪，不调参）。

问题：生产定义「横盘吸筹（20–125 日箱体）→ 放量突破」在信号日**次日开盘**
入场后的条件收益，是否真的优于同一时点、同行业、同市值/波动/动量/换手
的可比股票？如果比不过匹配对照，A 池的形态层就没有条件信息，后续任何
组合/执行优化都建立在噪声上。

方法（全部预定义，跑完不改）：
1. 事件：用生产检测器 `signals.detect_accumulation_breakout`（strict，
   最松台阶 box_min_days=20）在固定分层股票样本上按 step 交易日重放；
   每个 (code, breakout_date) 只记一次。
2. 对照：事件日同日截面，剔除事件股、真实事件股 ±5 日、伪事件股；
   同行业优先（同行业候选 ≥ 6 才用），按 log(市值)/20 日波动/20 日动量/
   换手的稳健 z 距离取 5 个最近邻。
3. 收益：次日开盘入场，+H 收盘出场，pct_chg 连乘（含分红送转）。
4. 统计：按事件日聚类的 block bootstrap 95% CI；另有伪事件安慰剂
   （同一股票、远离真实事件的随机采样日）作为零分布。
5. 产出：runtime/research/event-study-* 下 manifest / events / diffs /
   report.md；结论只有 NO_CONDITIONAL_EDGE / CONDITIONAL_EDGE_CANDIDATE /
   INSUFFICIENT_SAMPLE 三种，不生成候选、不改每日参数。

用法：
  .venv312\\Scripts\\python.exe -X utf8 scripts/event_study_matched.py \\
      --codes 1200 --workers 4 --step 5 --start 20180101 --end 20260914
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.pop("PYTHONPATH", None)

from ab_screener.research.event_study import (
    DEFAULT_WEIGHTS,
    FEATURE_COLUMNS,
    HORIZONS,
    cross_section_features,
    next_open_forward_returns,
    select_controls,
    summarize_diffs,
)
from signals import detect_accumulation_breakout

_TZ = ZoneInfo("Asia/Shanghai")
STUDY_VERSION = "event-study-v1"
MIN_BARS_BEFORE_SIGNAL = 150
BLOCK_DAYS_AROUND_EVENT = 5
PLACEBO_MIN_STEPS_AWAY = 4

_W: dict = {}


# ────────────────────────────── 数据接入 ──────────────────────────────

def _ro_uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=60)
    con.execute("PRAGMA query_only=1")
    return con


def _trading_dates(con: sqlite3.Connection, start: str, end: str, step: int) -> list[str]:
    rows = con.execute(
        "SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date>=? AND cal_date<=? "
        "ORDER BY cal_date",
        (start, end),
    ).fetchall()
    dates = [str(r[0]) for r in rows]
    if not dates:
        rows = con.execute(
            "SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=? "
            "ORDER BY trade_date",
            (start, end),
        ).fetchall()
        dates = [str(r[0]) for r in rows]
    if not dates:
        raise SystemExit("交易日历为空：无法确定重放样本日")
    return dates[:: max(1, step)]


def _latest_trade_date(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    if not row or not row[0]:
        raise SystemExit("daily 为空：没有可研究的行情")
    return str(row[0])


def _sample_universe(con: sqlite3.Connection, target: int, seed: int) -> list[str]:
    """固定种子、按行业分层的样本股票池（不看未来收益，只按行业+随机排序）。"""
    basic = pd.read_sql_query(
        "SELECT ts_code, name, industry, list_date, market FROM stock_basic", con
    )
    basic = basic[basic["ts_code"].astype(str).str.endswith((".SH", ".SZ"))]
    basic = basic[~basic["ts_code"].astype(str).str[:3].isin(["200", "900"])]
    basic = basic[basic["list_date"].astype(str).str.zfill(8) <= "20170101"]
    if basic.empty:
        raise SystemExit("stock_basic 没有满足上市时间条件的股票")
    rng = random.Random(seed)
    basic = basic.assign(
        _rand=[rng.random() for _ in range(len(basic))],
        _industry=basic["industry"].fillna("未分类").astype(str),
    )
    total = len(basic)
    alloc = {
        industry: max(1, round(target * len(group) / total))
        for industry, group in basic.groupby("_industry")
    }
    picked: list[str] = []
    for industry, group in basic.groupby("_industry"):
        picked.extend(group.sort_values("_rand")["ts_code"].head(alloc[industry]).tolist())
    return sorted(dict.fromkeys(picked))


def _chunks(items: list[str], size: int = 200):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ────────────────────────────── 事件重放 ──────────────────────────────

def _worker_init(db_path: str, end: str) -> None:
    _W["con"] = sqlite3.connect(_ro_uri(Path(db_path)), uri=True, timeout=60)
    _W["end"] = end


def _detect_code(task: tuple[str, list[str]]) -> list[dict]:
    """重放单只股票：每个 sample_date 用截止当日的历史调用生产检测器。"""
    code, sample_dates = task
    con: sqlite3.Connection = _W["con"]
    frame = pd.read_sql_query(
        "SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol "
        "FROM daily WHERE ts_code=? AND trade_date<=? ORDER BY trade_date",
        con,
        params=(code, _W["end"]),
    )
    if len(frame) < MIN_BARS_BEFORE_SIGNAL:
        return []
    frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    dates = frame["trade_date"].astype(str).to_numpy()
    found: dict[str, dict] = {}
    for sample_date in sample_dates:
        pos = int(np.searchsorted(dates, sample_date, side="right")) - 1
        if pos < MIN_BARS_BEFORE_SIGNAL or pos >= len(dates) or dates[pos] != sample_date:
            continue
        window = frame.iloc[max(0, pos - 220):pos + 1]
        sig = detect_accumulation_breakout(window, box_min_days=20, require_structure=True)
        if not sig.get("is_breakout"):
            continue
        breakout_date = str(sig.get("breakout_date") or "").replace("-", "")
        if not breakout_date or breakout_date in found:
            continue
        found[breakout_date] = {
            "ts_code": code,
            "signal_date": sample_date,
            "breakout_date": breakout_date,
            "box_days": int(sig.get("box_days") or 0),
            "box_amp": None if sig.get("box_amp") is None else float(sig["box_amp"]),
            "breakout_vol_ratio": None if sig.get("breakout_vol_ratio") is None else float(sig["breakout_vol_ratio"]),
            "breakout_pct_chg": None if sig.get("breakout_pct_chg") is None else float(sig["breakout_pct_chg"]),
            "box_high": None if sig.get("box_high") is None else float(sig["box_high"]),
            "box_low": None if sig.get("box_low") is None else float(sig["box_low"]),
        }
    return list(found.values())


# ────────────────────────────── 面板与匹配 ──────────────────────────────

def _build_panel(
    con: sqlite3.Connection,
    codes: list[str],
    panel_dates: list[str],
    *,
    on_blocked=None,
) -> dict[str, dict]:
    """只保留 panel_dates 当天的截面：特征 + 前视收益。内存 O(日期×样本数)。"""
    panel_dates_arr = np.asarray([int(d) for d in panel_dates], dtype=np.int64)
    code_pos = {code: i for i, code in enumerate(codes)}
    basic = pd.read_sql_query("SELECT ts_code, industry FROM stock_basic", con)
    industry = {str(r.ts_code): str(r.industry or "未分类") for r in basic.itertuples()}

    panel: dict[str, dict] = {
        d: {
            "code": [], "industry": [], "log_mv": [], "vol20": [], "mom20": [],
            "turnover": [], **{f"fwd{h}": [] for h in HORIZONS},
        }
        for d in panel_dates
    }

    for chunk in _chunks(codes):
        marks = ",".join("?" * len(chunk))
        daily = pd.read_sql_query(
            f"SELECT ts_code, trade_date, open, close, pre_close, pct_chg "
            f"FROM daily WHERE ts_code IN ({marks}) ORDER BY ts_code, trade_date",
            con,
            params=chunk,
        ).drop_duplicates(["ts_code", "trade_date"], keep="last")
        basicq = pd.read_sql_query(
            f"SELECT ts_code, trade_date, total_mv, turnover_rate FROM daily_basic "
            f"WHERE ts_code IN ({marks}) ORDER BY ts_code, trade_date",
            con,
            params=chunk,
        ).drop_duplicates(["ts_code", "trade_date"], keep="last")
        for code, dgroup in daily.groupby("ts_code", sort=False):
            bgroup = basicq[basicq["ts_code"] == code].set_index("trade_date")
            bgroup = bgroup.reindex(dgroup["trade_date"].to_numpy())
            close = dgroup["close"].to_numpy(dtype=np.float64)
            pct = dgroup["pct_chg"].to_numpy(dtype=np.float64)
            feats = cross_section_features(
                close,
                pct,
                bgroup["total_mv"].to_numpy(dtype=np.float64),
                bgroup["turnover_rate"].to_numpy(dtype=np.float64),
            )
            fwds = next_open_forward_returns(
                dgroup["open"].to_numpy(dtype=np.float64),
                dgroup["pre_close"].to_numpy(dtype=np.float64),
                pct,
                horizons=HORIZONS,
            )
            dates = pd.to_numeric(dgroup["trade_date"], errors="coerce").to_numpy(dtype=np.int64)
            pos = np.searchsorted(dates, panel_dates_arr)
            in_range = pos < len(dates)
            pos = pos[in_range]
            wanted = panel_dates_arr[in_range]
            hit = dates[pos] == wanted if len(pos) else np.zeros(0, dtype=bool)
            if on_blocked is not None and len(pos):
                on_blocked(code, dates, pos, hit)
            cidx = code_pos[code]
            ind = industry.get(code, "未分类")
            for p in pos[hit]:
                day = str(dates[p])
                bucket = panel[day]
                bucket["code"].append(cidx)
                bucket["industry"].append(ind)
                for name in FEATURE_COLUMNS:
                    bucket[name].append(float(feats[name][p]))
                for h in HORIZONS:
                    bucket[f"fwd{h}"].append(float(fwds[h][p]))
    return panel


def _prepare_day_frame(panel, day: str) -> pd.DataFrame | None:
    """构造某日的匹配池（特征齐全）；同日多个事件共用，避免重复 DataFrame。"""
    bucket = panel.get(day)
    if not bucket or len(bucket["code"]) < 3:
        return None
    frame = pd.DataFrame({name: bucket[name] for name in FEATURE_COLUMNS})
    frame["_code"] = np.asarray(bucket["code"], dtype=np.int64)
    frame["_industry"] = np.asarray(bucket["industry"], dtype=object)
    for h in HORIZONS:
        frame[f"fwd{h}"] = bucket[f"fwd{h}"]
    return frame.dropna(subset=list(FEATURE_COLUMNS))


def _match_event(
    frame: pd.DataFrame,
    event_code_idx: int,
    event_vector: dict[str, float],
    event_fwds: dict[int, float],
    *,
    blocked_codes: set[int],
    k: int,
) -> dict[int, dict] | None:
    pool = frame[~frame["_code"].isin(blocked_codes)]
    pool = pool[pool["_code"] != event_code_idx]
    if len(pool) < k + 1:
        return None
    same = pool[pool["_industry"] == event_vector["industry"]]
    if len(same) >= k + 1:
        pool = same
    pick, _dist = select_controls(
        {name: event_vector[name] for name in FEATURE_COLUMNS},
        pool.set_index(np.arange(len(pool)))[list(FEATURE_COLUMNS)],
        k=k,
        weights=DEFAULT_WEIGHTS,
    )
    if not pick:
        return None
    chosen = pool.iloc[pick]
    out: dict[int, dict] = {}
    for h in HORIZONS:
        ev = event_fwds.get(h)
        ctrl = chosen[f"fwd{h}"].to_numpy(dtype=np.float64)
        ctrl = ctrl[np.isfinite(ctrl)]
        if ev is None or not np.isfinite(ev) or len(ctrl) < 3:
            continue
        out[h] = {
            "event": float(ev),
            "control": float(ctrl.mean()),
            "diff": float(ev - ctrl.mean()),
            "n_controls": int(len(ctrl)),
        }
    return out or None


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ────────────────────────────── 主流程 ──────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Phase 1 matched-control event study")
    p.add_argument("--db", type=Path, default=_ROOT / "runtime" / "stock_data.db")
    p.add_argument("--start", default="20180101")
    p.add_argument("--end", default=None, help="默认取库内最新交易日")
    p.add_argument("--codes", type=int, default=1200, help="分层样本股票数")
    p.add_argument("--step", type=int, default=5, help="每隔几个交易日采样（检测窗=5）")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--k", type=int, default=5, help="每个事件的对照数量")
    p.add_argument("--placebo", type=int, default=1, help="每事件伪事件数量")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--min-events", type=int, default=100, help="低于该事件数直接 INSUFFICIENT_SAMPLE")
    p.add_argument("--seed", type=int, default=20260914)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"数据库不存在: {args.db}")
    con = _connect(args.db)
    try:
        end = str(args.end or _latest_trade_date(con))
        sample_dates = _trading_dates(con, str(args.start), end, args.step)
        codes = _sample_universe(con, args.codes, args.seed)
    finally:
        con.close()
    if len(codes) < 50:
        raise SystemExit(f"样本股票过少: {len(codes)}")

    stamp = datetime.now(_TZ).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or (_ROOT / "runtime" / "research" / f"event-study-v1-{stamp}")
    if out_dir.exists():
        raise SystemExit(f"输出目录已存在，拒绝覆盖: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)

    t0 = time.time()
    print(f"[study] codes={len(codes)} sample_dates={len(sample_dates)} {sample_dates[0]}..{sample_dates[-1]}")
    print(f"[study] end={end} workers={args.workers} step={args.step} k={args.k}")

    # 1) 事件重放
    events: list[dict] = []
    with ProcessPoolExecutor(
        max_workers=max(1, args.workers),
        initializer=_worker_init,
        initargs=(str(args.db), end),
    ) as pool:
        for i, chunk in enumerate(pool.map(_detect_code, [(c, sample_dates) for c in codes], chunksize=1), start=1):
            events.extend(chunk)
            if i % 200 == 0:
                print(f"[study] replay {i}/{len(codes)} events={len(events)} elapsed={time.time()-t0:.0f}s", flush=True)
    events.sort(key=lambda e: (e["signal_date"], e["ts_code"]))
    print(f"[study] replay done: events={len(events)} stocks={len({e['ts_code'] for e in events})} elapsed={time.time()-t0:.0f}s")

    if len(events) < args.min_events:
        _write_json(out_dir / "manifest.json", {"status": "INSUFFICIENT_SAMPLE", "n_events": len(events), "args": vars(args) | {"db": str(args.db), "out": str(out_dir)}})
        print(f"[study] 事件不足 {args.min_events}，停止（INSUFFICIENT_SAMPLE）")
        return 2

    # 2) 伪事件：同一股票、距真实事件至少 PLACEBO_MIN_STEPS_AWAY 个采样步
    rng = random.Random(args.seed)
    sample_index = {d: i for i, d in enumerate(sample_dates)}
    real_by_code: dict[str, set[str]] = defaultdict(set)
    for e in events:
        real_by_code[e["ts_code"]].add(e["signal_date"])
    pseudo: list[dict] = []
    for code, real_dates in real_by_code.items():
        banned = set()
        for d in real_dates:
            idx = sample_index.get(d)
            if idx is None:
                continue
            for j in range(max(0, idx - PLACEBO_MIN_STEPS_AWAY), min(len(sample_dates), idx + PLACEBO_MIN_STEPS_AWAY + 1)):
                banned.add(sample_dates[j])
        for _ in range(args.placebo * len(real_dates)):
            for _attempt in range(20):
                cand = sample_dates[rng.randrange(len(sample_dates))]
                if cand not in banned:
                    pseudo.append({"ts_code": code, "signal_date": cand})
                    break
    pseudo = [dict(e, pseudo=True) for e in pseudo]
    print(f"[study] placebo={len(pseudo)}")

    need_dates = sorted({e["signal_date"] for e in events} | {e["signal_date"] for e in pseudo})
    con = _connect(args.db)
    try:
        # 事件向量（复用面板：事件日的特征/收益）
        panel = _build_panel(con, codes, need_dates)
        code_pos = {c: i for i, c in enumerate(codes)}
        lookup: dict[tuple[str, str], dict] = {}
        for day, bucket in panel.items():
            for i, cidx in enumerate(bucket["code"]):
                lookup[(codes[cidx], day)] = {
                    "vector": {name: bucket[name][i] for name in FEATURE_COLUMNS},
                    "fwds": {h: bucket[f"fwd{h}"][i] for h in HORIZONS},
                    "industry": bucket["industry"][i],
                }

        # 屏蔽：真实事件 ±5 采样子，以及伪事件当日
        blocked: dict[str, set[int]] = defaultdict(set)
        for e in events:
            idx = sample_index.get(e["signal_date"])
            if idx is None:
                continue
            for j in range(max(0, idx - BLOCK_DAYS_AROUND_EVENT), min(len(sample_dates), idx + BLOCK_DAYS_AROUND_EVENT + 1)):
                blocked[sample_dates[j]].add(code_pos[e["ts_code"]])
        for e in pseudo:
            blocked[e["signal_date"]].add(code_pos[e["ts_code"]])

        frame_cache: dict[str, pd.DataFrame | None] = {}

        def collect(items: list[dict]) -> dict[int, list[dict]]:
            out = {h: [] for h in HORIZONS}
            used = 0
            for e in items:
                key = (e["ts_code"], e["signal_date"])
                got = lookup.get(key)
                if not got:
                    continue
                day = e["signal_date"]
                if day not in frame_cache:
                    frame_cache[day] = _prepare_day_frame(panel, day)
                base_frame = frame_cache[day]
                if base_frame is None or base_frame.empty:
                    continue
                vec = {**got["vector"], "industry": got["industry"]}
                matched = _match_event(
                    base_frame, code_pos[e["ts_code"]], vec, got["fwds"],
                    blocked_codes=blocked[day], k=args.k,
                )
                if not matched:
                    continue
                used += 1
                for h, row in matched.items():
                    out[h].append({"date": day, "ts_code": e["ts_code"], **row})
            print(f"[study] matched {used}/{len(items)}")
            return out

        event_rows = collect(events)
        placebo_rows = collect(pseudo)
    finally:
        con.close()

    # 3) 统计
    def stats_block(rows: dict[int, list[dict]]) -> dict[str, dict]:
        return {
            str(h): summarize_diffs(
                [r["date"] for r in rows.get(h, [])],
                [r["diff"] for r in rows.get(h, [])],
                reps=args.bootstrap,
                seed=args.seed,
            )
            for h in HORIZONS
        }

    event_stats = stats_block(event_rows)
    placebo_stats = stats_block(placebo_rows)
    raw_stats = {
        str(h): summarize_diffs(
            [r["date"] for r in event_rows.get(h, [])],
            [r["event"] for r in event_rows.get(h, [])],
            reps=args.bootstrap,
            seed=args.seed,
        )
        for h in HORIZONS
    }
    control_stats = {
        str(h): summarize_diffs(
            [r["date"] for r in event_rows.get(h, [])],
            [r["control"] for r in event_rows.get(h, [])],
            reps=args.bootstrap,
            seed=args.seed,
        )
        for h in HORIZONS
    }

    # 4) 裁决（预定义）
    h20 = event_stats.get("20", {})
    n20 = int(h20.get("n") or 0)
    if n20 < 300:
        verdict = "INSUFFICIENT_SAMPLE"
    elif h20.get("lo", 0) > 0 and (h20.get("mean", 0) - placebo_stats.get("20", {}).get("mean", 0)) > 0.005:
        verdict = "CONDITIONAL_EDGE_CANDIDATE"
    else:
        verdict = "NO_CONDITIONAL_EDGE"

    manifest = {
        "study_version": STUDY_VERSION,
        "verdict": verdict,
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "db": str(args.db),
        "start": sample_dates[0],
        "end": end,
        "step": args.step,
        "seed": args.seed,
        "k_controls": args.k,
        "n_codes": len(codes),
        "n_sample_dates": len(sample_dates),
        "n_events": len(events),
        "n_event_stocks": len({e["ts_code"] for e in events}),
        "n_placebo": len(pseudo),
        "n_placebo_matched": len(placebo_rows.get(20, [])),
        "entry_definition": "next_open_entry__close_exit__pct_chg_chained",
        "detector": "detect_accumulation_breakout(box_min_days=20, require_structure=True)",
        "event_rule": "dedupe by (stock, breakout_date); sample_dates every step",
        "control_rule": "same-day, same-industry preferred (>=k+1), robust-z nearest by log_mv/vol20/mom20/turnover; block event stock and +/-5 sample steps",
        "bootstrap": {"reps": args.bootstrap, "cluster": "signal_date", "seed": args.seed},
        "runtime_sec": round(time.time() - t0, 1),
        "code_sha256": {
            "research/event_study.py": _sha256_file(_ROOT / "ab_screener" / "research" / "event_study.py"),
            "scripts/event_study_matched.py": _sha256_file(Path(__file__).resolve()),
        },
    }
    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "events.json", events)
    _write_json(out_dir / "stats.json", {
        "event_diff": event_stats,
        "placebo_diff": placebo_stats,
        "event_raw": raw_stats,
        "control_raw": control_stats,
    })
    _write_json(out_dir / "diffs.json", {
        "event": {str(h): rows for h, rows in event_rows.items()},
        "placebo": {str(h): rows for h, rows in placebo_rows.items()},
    })

    lines = [
        f"# Phase 1 匹配对照事件研究（{STUDY_VERSION}）",
        "",
        f"- 裁决：**{verdict}**",
        f"- 样本：{len(codes)} 只（固定种子分层）× {len(sample_dates)} 个采样日（{sample_dates[0]}..{sample_dates[-1]}，step={args.step}）",
        f"- 事件：{len(events)} 个 strict 形态（{len({e['ts_code'] for e in events})} 只股票）；伪事件 {len(pseudo)}",
        f"- 入场/出场：信号日次日开盘 → +H 收盘（pct_chg 连乘）；对照 k={args.k}",
        f"- 统计：按 signal_date 聚类 block bootstrap {args.bootstrap} 次",
        "",
        "## 事件减匹配对照（主要结果）",
        "",
        "| H | n | 事件日数 | 均值 | 中位 | 95% CI 低 | 95% CI 高 | 为正占比 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for h in HORIZONS:
        s = event_stats[str(h)]
        lines.append(
            f"| {h} | {s.get('n',0)} | {s.get('n_dates',0)} | {s.get('mean',float('nan'))*100:+.2f}% | "
            f"{s.get('median',float('nan'))*100:+.2f}% | {s.get('lo',float('nan'))*100:+.2f}% | "
            f"{s.get('hi',float('nan'))*100:+.2f}% | {s.get('positive_rate',0)*100:.1f}% |"
        )
    lines += ["", "## 一致性检查", "", "| H | 事件原收益 | 对照原收益 | 伪事件收益差 | 伪事件 95% CI |", "|---|---:|---:|---:|---:|"]
    for h in HORIZONS:
        r, c, pl = raw_stats[str(h)], control_stats[str(h)], placebo_stats[str(h)]
        lines.append(
            f"| {h} | {r.get('mean',float('nan'))*100:+.2f}% | {c.get('mean',float('nan'))*100:+.2f}% | "
            f"{pl.get('mean',float('nan'))*100:+.2f}% | [{pl.get('lo',float('nan'))*100:+.2f}%, {pl.get('hi',float('nan'))*100:+.2f}%] |"
        )
    lines += [
        "",
        "## 事件画像",
        "",
        f"- 箱体天数：中位 {int(np.median([e['box_days'] for e in events]))}，P10={int(np.percentile([e['box_days'] for e in events],10))}，P90={int(np.percentile([e['box_days'] for e in events],90))}",
        f"- 突破量比：中位 {np.median([e['breakout_vol_ratio'] for e in events if e['breakout_vol_ratio']]):.2f}",
        f"- 运行时间：{manifest['runtime_sec']}s",
        "",
        "## 边界（必须随结论一起引用）",
        "",
        "- 未计费用、滑点、涨跌停/停牌不可成交性；只回答“条件收益是否存在”这一层。",
        "- 使用当前修订数据重放历史行情，存在轻度的 restatement 偏差，不等于当年实时 PIT。",
        "- 停牌期间持有期按个股自身交易日顺延；对照池仅限本研究的固定分层样本。",
        "- 无论结果如何都不自动修改每日选股参数、不生成候选、不进入 A 池。",
    ]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[study] verdict={verdict}")
    print(f"[study] artifact={out_dir}")
    print(f"[study] elapsed={time.time()-t0:.0f}s")
    return 0 if verdict != "INSUFFICIENT_SAMPLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
