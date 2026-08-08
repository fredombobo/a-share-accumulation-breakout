"""数据驱动研究窗口（平台突破个人研究）

按本地日线覆盖自动选择：
  - full：完整 24 月 IS + 12 月 OOS（config BT_*）
  - degraded：库内可用区间按时间切分（约 65% IS / 35% OOS）
  - insufficient：交易日过少，禁止声称有统计意义

用法：
  from research_windows import recommend_research_plan, research_status_dict
  plan = recommend_research_plan()
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from config import (
    BT_IS_END,
    BT_IS_START,
    BT_OOS_END,
    BT_OOS_START,
    HISTORY_SYNC_DAYS,
    WF_MIN_OOS_PF_RATIO,
)

# 完整窗所需约 3 年交易日
FULL_MIN_DATES = 720
# 降级研究最低门槛（约 9 个月）
DEGRADED_MIN_DATES = 180
# 降级窗内：样本内/外最少交易日
MIN_IS_DATES = 100
MIN_OOS_DATES = 40


@dataclass
class ResearchPlan:
    mode: str  # full | degraded | insufficient
    label: str
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    n_dates: int
    earliest: str | None
    latest: str | None
    is_n_dates: int
    oos_n_dates: int
    wf_windows: list[tuple[str, str, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    can_claim_edge: bool = False  # 仅 full 模式且 n_dates 够才允许「有 edge」表述
    target_history_days: int = HISTORY_SYNC_DAYS

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["wf_windows"] = [
            {"train_start": a, "train_end": b, "test_start": c, "test_end": d_}
            for a, b, c, d_ in self.wf_windows
        ]
        return d


def _load_dates() -> list[str]:
    from local_store import LocalStore

    return list(LocalStore().distinct_dates("daily") or [])


def _clip_window(
    dates: list[str], start: str, end: str
) -> tuple[str, str, int] | None:
    """把 [start,end] 裁到库内有数据的子区间；无重叠则 None。"""
    in_range = [d for d in dates if start <= d <= end]
    if not in_range:
        return None
    return in_range[0], in_range[-1], len(in_range)


def _build_wf_from_dates(dates: list[str], n_windows: int = 3) -> list[tuple[str, str, str, str]]:
    """在可用交易日上切 3 个滚动 train/test（约 2:1）。"""
    n = len(dates)
    if n < MIN_IS_DATES + MIN_OOS_DATES:
        return []
    # 每个窗口占约 45% 长度，步进约 20%
    win = max(MIN_IS_DATES + MIN_OOS_DATES, int(n * 0.45))
    train_len = max(MIN_IS_DATES, int(win * 0.67))
    test_len = max(MIN_OOS_DATES, win - train_len)
    step = max(20, int(n * 0.18))
    out: list[tuple[str, str, str, str]] = []
    start_i = 0
    while start_i + train_len + test_len <= n and len(out) < n_windows:
        te = start_i + train_len - 1
        vs = te + 1
        ve = vs + test_len - 1
        out.append((dates[start_i], dates[te], dates[vs], dates[ve]))
        start_i += step
    # 若步进切不出 3 个，尽量补尾窗
    if len(out) < n_windows and n >= train_len + test_len:
        start_i = n - train_len - test_len
        tail = (dates[start_i], dates[start_i + train_len - 1],
                dates[start_i + train_len], dates[n - 1])
        if not out or out[-1] != tail:
            out.append(tail)
    return out[:n_windows]


def recommend_research_plan(dates: list[str] | None = None) -> ResearchPlan:
    """根据日线覆盖推荐 IS/OOS/WF 窗口。"""
    dates = sorted(dates if dates is not None else _load_dates())
    n = len(dates)
    earliest = dates[0] if dates else None
    latest = dates[-1] if dates else None
    notes: list[str] = []

    if n < DEGRADED_MIN_DATES or not earliest or not latest:
        return ResearchPlan(
            mode="insufficient",
            label="数据不足",
            is_start=BT_IS_START,
            is_end=BT_IS_END,
            oos_start=BT_OOS_START,
            oos_end=BT_OOS_END,
            n_dates=n,
            earliest=earliest,
            latest=latest,
            is_n_dates=0,
            oos_n_dates=0,
            wf_windows=[],
            notes=[
                f"仅 {n} 个交易日（需 ≥{DEGRADED_MIN_DATES} 才可做降级研究）",
                f"请配置有效 TUSHARE_TOKEN 后执行: python sync_history.py  # 目标 {HISTORY_SYNC_DAYS} 日",
            ],
            can_claim_edge=False,
        )

    # 尝试完整窗（config 目标）
    is_full = _clip_window(dates, BT_IS_START, BT_IS_END)
    oos_full = _clip_window(dates, BT_OOS_START, BT_OOS_END)
    full_ok = (
        n >= FULL_MIN_DATES
        and is_full is not None
        and oos_full is not None
        and is_full[2] >= 400
        and oos_full[2] >= 180
        and earliest <= BT_IS_START
        and latest >= BT_OOS_END
    )
    if full_ok and is_full and oos_full:
        # 标准 WF（walkforward.WF_WINDOWS），裁到有数据
        from walkforward import WF_WINDOWS

        wf: list[tuple[str, str, str, str]] = []
        for ts, te, vs, ve in WF_WINDOWS:
            tr = _clip_window(dates, ts, te)
            tev = _clip_window(dates, vs, ve)
            if tr and tev and tr[2] >= 60 and tev[2] >= 30:
                wf.append((tr[0], tr[1], tev[0], tev[1]))
        notes.append("完整 24 月 IS + 12 月 OOS，可严肃讨论参数稳定性")
        return ResearchPlan(
            mode="full",
            label="完整验证窗",
            is_start=is_full[0],
            is_end=is_full[1],
            oos_start=oos_full[0],
            oos_end=oos_full[1],
            n_dates=n,
            earliest=earliest,
            latest=latest,
            is_n_dates=is_full[2],
            oos_n_dates=oos_full[2],
            wf_windows=wf or _build_wf_from_dates(dates),
            notes=notes,
            can_claim_edge=True,
        )

    # 降级：时间序列前 65% IS，后 35% OOS（严格不交叉）
    split = max(MIN_IS_DATES, int(n * 0.65))
    if n - split < MIN_OOS_DATES:
        split = n - MIN_OOS_DATES
    if split < MIN_IS_DATES:
        return ResearchPlan(
            mode="insufficient",
            label="区间过短",
            is_start=earliest,
            is_end=latest,
            oos_start=earliest,
            oos_end=latest,
            n_dates=n,
            earliest=earliest,
            latest=latest,
            is_n_dates=n,
            oos_n_dates=0,
            notes=["可用区间不足以同时容纳 IS/OOS 最小长度"],
            can_claim_edge=False,
        )

    is_start, is_end = dates[0], dates[split - 1]
    oos_start, oos_end = dates[split], dates[-1]
    is_n, oos_n = split, n - split
    notes.append(
        f"降级窗：库内 {earliest}~{latest}（{n} 日），IS≈{is_n} 日 / OOS≈{oos_n} 日"
    )
    notes.append(
        f"完整验证需 ≥{FULL_MIN_DATES} 日并覆盖 {BT_IS_START}~{BT_OOS_END}；"
        f"请 python sync_history.py（需有效 Token）"
    )
    notes.append("降级结果仅供摸底，不可当作已验证 edge 或实盘参数依据")
    if earliest > BT_IS_START:
        notes.append(f"缺少 {BT_IS_START}~{earliest} 之前历史，当前 earliest={earliest}")

    return ResearchPlan(
        mode="degraded",
        label="降级研究窗",
        is_start=is_start,
        is_end=is_end,
        oos_start=oos_start,
        oos_end=oos_end,
        n_dates=n,
        earliest=earliest,
        latest=latest,
        is_n_dates=is_n,
        oos_n_dates=oos_n,
        wf_windows=_build_wf_from_dates(dates),
        notes=notes,
        can_claim_edge=False,
    )


def probe_tushare_token() -> dict[str, Any]:
    """轻量探测 Token 是否可用（不拉全市场）。"""
    try:
        from tushare_init import pro

        df = pro.trade_cal(exchange="SSE", start_date="20260801", end_date="20260806")
        ok = df is not None and len(df) > 0
        return {"ok": bool(ok), "error": None if ok else "empty response", "n_rows": 0 if df is None else len(df)}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # 截断避免把完整 token 打进日志（部分错误会回显 token）
        if "您传过来的是" in msg:
            msg = "token不对（请更新 .env 中 TUSHARE_TOKEN）"
        return {"ok": False, "error": msg[:200], "n_rows": 0}


def research_status_dict(probe_token: bool = True) -> dict[str, Any]:
    """研究就绪状态（CLI / API 共用）。"""
    plan = recommend_research_plan()
    token = probe_tushare_token() if probe_token else {"ok": None, "error": "skipped", "n_rows": 0}
    need_backfill = plan.mode != "full"
    next_steps: list[str] = []
    if not token.get("ok"):
        next_steps.append("1. 到 tushare.pro 复制有效 Token，写入 .env 的 TUSHARE_TOKEN")
        next_steps.append("2. python sync_history.py   # 断点续传扩到约 730 交易日（2~4 小时）")
    elif need_backfill:
        next_steps.append("1. python sync_history.py   # Token 可用，开始历史扩容")
        next_steps.append("2. python research_status.py  # 确认 mode=full 后再严肃优化")
    else:
        next_steps.append("1. python run_optimize_plan.py A 600 10")
        next_steps.append("2. python pipeline_seed.py A 600 10")
        next_steps.append("3. 界面 /lab 查看排行榜；A 池仍以扫描+防守环境为准")

    return {
        "as_of_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plan": plan.to_dict(),
        "token": token,
        "need_backfill": need_backfill,
        "next_steps": next_steps,
        "disclaimer": (
            "本系统为个人研究辅助，不是投资建议。"
            "策略实验室参数不可直接等同 A 池可交易名单；"
            "仅 mode=full 且 OOS/WF 通过后，才可讨论参数稳定性。"
        ),
        "wf_min_oos_pf_ratio": WF_MIN_OOS_PF_RATIO,
    }
