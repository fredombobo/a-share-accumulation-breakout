"""
全市场扫描主流程（胜率优先）—— 公共 facade
============================================
业务实现已迁入 ab_screener/screener（V2R-A 职责拆分）：
  - screener.data_loader   只读/标准化输入（load_market_data）
  - screener.prefilter     候选集合 + 理由（prefilter）
  - screener.evaluator     单标的结果（阶梯/打分/软分/主题观察/信号检测）
  - screener.orchestrator  进程/取消/进度/排序/聚合（run_scan 主体）

本文件仅保留：CLI main + 公共入口转发 + 兼容常量，不承载业务实现。

用法：
  python run_screener.py --top 15 --days 160
  python run_screener.py --top 15 --no-watch
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"curl_cffi\..*")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)


from ab_screener.screener import (
    _detect_on_codes,
    _score_codes,
    _soft_setup_row,
    _theme_soft_fill,
    apply_box_ladder,
    load_market_data,
    observed_signal,
    prefilter,
    run_scan,
)
from ab_screener.screener.data_loader import CACHE_DIR, OUT_DIR
from ab_screener.screener.evaluator import (
    BOX_LADDER_DAYS,
    TARGET_SELECT_COUNT,
)
from ab_screener.screener.orchestrator import (
    _DEFAULT_THEME_MIN,
    SCAN_WORKERS,
)
from config import (
    HORIZON_DAYS,
    TOP_N,
)
from signals import detect_accumulation_breakout

__all__ = [
    "BOX_LADDER_DAYS",
    "CACHE_DIR",
    "OUT_DIR",
    "SCAN_WORKERS",
    "TARGET_SELECT_COUNT",
    "_DEFAULT_THEME_MIN",
    "_detect_on_codes",
    "_score_codes",
    "_soft_setup_row",
    "_theme_soft_fill",
    "apply_box_ladder",
    "detect_accumulation_breakout",
    "load_market_data",
    "main",
    "observed_signal",
    "prefilter",
    "run_scan",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="横盘吸筹→启动 选股（A池可交易 / B池观察）")
    parser.add_argument("--top", type=int, default=TOP_N, help="A 池数量（默认15）")
    parser.add_argument("--days", type=int, default=HORIZON_DAYS, help="回看天数")
    parser.add_argument("--force", action="store_true", help="强制重新拉取数据")
    parser.add_argument("--max-check", type=int, default=None, help="限制检查数量（调试用）")
    parser.add_argument("--no-watch", action="store_true", help="不构建 B 观察池")
    parser.add_argument("--relaxed-in-a", action="store_true", help="允许 relaxed 进入 A 池")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="并行进程数（0=自动，1=单进程；默认读 config.SCAN_WORKERS）",
    )
    args = parser.parse_args()

    result = run_scan(
        top=args.top,
        days=args.days,
        force=args.force,
        max_check=args.max_check,
        build_watch=not args.no_watch,
        include_relaxed_in_a=args.relaxed_in_a,
        workers=args.workers,
    )

    df = result["df"]
    print(f"\n===== 扫描完成（{result['elapsed_sec']}s，workers={result.get('workers')}）=====")
    print(f"最新交易日: {result['latest_date']}  新鲜度: {result.get('freshness')}")
    print(f"环境: {result.get('regime')}")
    print(f"A池: {result.get('pool_report', {}).get('a_count')}  B池: {result.get('pool_report', {}).get('b_count')}")
    if df is not None and not df.empty:
        cols = [c for c in ["代码", "名称", "池", "筛选层级", "主题板块", "最新价", "综合分", "止损价", "目标1", "建议仓位%", "可交易"] if c in df.columns]
        print("\n-- A 池可交易 --")
        print(df[cols].to_string(index=False))
    dfb = result.get("df_b")
    if dfb is not None and not dfb.empty:
        cols = [c for c in ["代码", "名称", "池", "筛选层级", "主题板块", "综合分"] if c in dfb.columns]
        print("\n-- B 池观察(前10) --")
        print(dfb[cols].head(10).to_string(index=False))
    # 防守空 A 池也算成功完成
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
