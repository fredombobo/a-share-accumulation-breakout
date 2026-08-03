"""
K线图生成
=========
用 mplfinance 绘制近 N 日K线，标注：
  - 横盘吸筹箱体区间（矩形阴影 + 上沿/下沿虚线）
  - 突破日（竖线 + 标注）
  - 成交量副图（红涨绿跌）
  - MA5 / MA20 均线
输出 PNG 到 out/charts/
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from config import CHART_DIR, HORIZON_DAYS
FONT_FAMILY = "Microsoft YaHei"
try:
    plt.rcParams["font.sans-serif"] = [FONT_FAMILY, "SimHei", "SimSun"]
    plt.rcParams["axes.unicode_minus"] = False
except Exception:  # noqa: BLE001
    pass


def prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """标准化为 mplfinance 所需的 OHLCV DataFrame（按日期升序，date 为索引）"""
    out = df.copy()
    vol_col = "vol" if "vol" in out.columns else "volume"
    # 腾讯格式: date, open, close, high, low, vol
    if "open" in out.columns and "close" in out.columns:
        o = pd.to_numeric(out["open"], errors="coerce")
        h = pd.to_numeric(out["high"], errors="coerce")
        l = pd.to_numeric(out["low"], errors="coerce")
        c = pd.to_numeric(out["close"], errors="coerce")
        v = pd.to_numeric(out[vol_col], errors="coerce").fillna(0)
    else:
        raise ValueError("K线数据缺少 OHLC 列")
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
    idx = pd.to_datetime(df["date"]) if "date" in df.columns else pd.to_datetime(out.index)
    out.index = pd.DatetimeIndex(idx)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def plot_kline(
    df: pd.DataFrame,
    ts_code: str,
    name: str,
    sig: dict,
    out_dir: str | Path | None = None,
    days: int | None = None,
) -> str:
    """绘制单只股票K线图，返回 PNG 路径。

    df: 原始K线（date/open/high/low/close/vol），按 date 升序
    sig: detect_accumulation_breakout 的返回值
    """
    out_dir = Path(out_dir or CHART_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    days = days or HORIZON_DAYS

    ohlcv = prepare_ohlcv(df.tail(days))
    if len(ohlcv) < 10:
        raise ValueError(f"{ts_code} K线不足10根")

    # 附加均线
    apds = [
        mpf.make_addplot(ohlcv["Close"].rolling(5).mean(), color="#f39c12", width=1.0, label="MA5"),
        mpf.make_addplot(ohlcv["Close"].rolling(20).mean(), color="#8e44ad", width=1.0, label="MA20"),
    ]

    # 箱体标注（若信号检测到）
    box_patch = None
    breakout_line = None
    if sig and sig.get("box_high"):
        # 箱体上沿/下沿：转换为数据坐标的横线
        box_high = sig["box_high"]
        box_low = sig["box_low"]
        # 箱体时间区间：起点按 box_days 估算，终点为突破日前一日（无突破则到图末）
        n = len(ohlcv)
        idx = ohlcv.index
        box_days = sig.get("box_days") or 40
        start_pos = max(0, n - box_days - 5)
        end_pos = n - 1
        if sig.get("breakout_date"):
            try:
                bpos = idx.get_loc(pd.to_datetime(sig["breakout_date"]))
                end_pos = max(start_pos, bpos - 1)
            except (KeyError, TypeError, ValueError):
                pass
        # 上沿/下沿线段只覆盖箱体区间（区间外置 NaN，避免画满整图）
        hi_line = pd.Series(float("nan"), index=idx)
        lo_line = pd.Series(float("nan"), index=idx)
        hi_line.iloc[start_pos:end_pos + 1] = box_high
        lo_line.iloc[start_pos:end_pos + 1] = box_low
        apds.append(mpf.make_addplot(
            hi_line, color="#e74c3c",
            width=0.8, linestyle="--", label="箱体上沿"))
        apds.append(mpf.make_addplot(
            lo_line, color="#27ae60",
            width=0.8, linestyle="--", label="箱体下沿"))
        box_patch = (start_pos, end_pos + 1, box_low, box_high)

    # 突破日竖线
    if sig and sig.get("breakout_date"):
        bd = pd.to_datetime(sig["breakout_date"])
        if bd in ohlcv.index:
            breakout_line = bd

    # 颜色：A股红涨绿跌
    mc = mpf.make_marketcolors(up="red", down="green", edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle=":",
        y_on_right=False,
        rc={"font.family": FONT_FAMILY, "font.sans-serif": [FONT_FAMILY, "SimHei"]},
    )

    fig, axes = mpf.plot(
        ohlcv,
        type="candle",
        style=style,
        addplot=apds,
        volume=True,
        figsize=(13, 8),
        returnfig=True,
        title=f"{name} ({ts_code})  突破日: {sig.get('breakout_date','-') if sig else '-'}",
        datetime_format="%m-%d",
        xrotation=15,
    )

    ax = axes[0]
    # 箱体阴影（axvspan 用日期位置）
    if box_patch:
        start_pos, end_pos, lo, hi = box_patch
        ax.axvspan(ohlcv.index[start_pos], ohlcv.index[min(end_pos, n-1)], alpha=0.10, color="gray")
    # 突破竖线
    if breakout_line is not None:
        ax.axvline(breakout_line, color="#e67e22", linestyle="-", linewidth=1.5, alpha=0.9)
        ax.annotate("突破", xy=(breakout_line, ohlcv.loc[breakout_line, "High"]),
                    xytext=(0, 18), textcoords="offset points",
                    ha="center", fontsize=11, color="#e67e22", fontweight="bold")
    ax.legend(loc="upper left", fontsize=9)

    fname = f"{ts_code.replace('.', '_')}.png"
    path = out_dir / fname
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_top_kline_batch(
    candidates: list[dict],
    df_by_code: dict[str, pd.DataFrame],
    sig_by_code: dict[str, dict],
    out_dir: str | Path | None = None,
) -> dict[str, str]:
    """批量绘制候选K线图。返回 {ts_code: png_path}"""
    out_dir = Path(out_dir or CHART_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for cand in candidates:
        code = cand["ts_code"]
        df = df_by_code.get(code)
        sig = sig_by_code.get(code, {})
        if df is None or df.empty:
            continue
        try:
            p = plot_kline(df, code, cand.get("name", ""), sig, out_dir=out_dir)
            paths[code] = p
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] K线图失败 {code}: {str(e)[:70]}")
    return paths


if __name__ == "__main__":
    # 快速自检：画一张平安银行
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import requests
    from config import TENCNET_KLINE_URL, UA
    r = requests.get(TENCNET_KLINE_URL,
                     params={"param": "sz000001,day,,,120,qfq"},
                     headers={"User-Agent": UA}, timeout=15)
    d = r.json()["data"]["sz000001"]
    key = "qfqday" if "qfqday" in d else "day"
    rows = [row[:6] for row in d[key]]
    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "vol"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    from signals import detect_accumulation_breakout
    sig = detect_accumulation_breakout(df)
    p = plot_kline(df, "000001.SZ", "平安银行", sig, out_dir="out/charts")
    print("K线图已生成:", p)
