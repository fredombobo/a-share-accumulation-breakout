"""
横盘吸筹→启动 选股系统 UI
==========================
Streamlit 交互面板：
  - 侧边栏：参数配置 + 运行扫描
  - Tab1 选股结果：Top N 表格 + 信号详情
  - Tab2 K线图浏览器：下拉选股 → 显示K线图（箱体/突破标注）
  - Tab3 资金流明细：近5日主力资金流
  - Tab4 策略报告：Markdown 报告

启动：streamlit run app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from config import OUT_DIR, TOP_N  # noqa: E402

st.set_page_config(page_title="A股 横盘吸筹→启动 选股系统", layout="wide", page_icon="📈")

# ── 会话状态 ──
if "scan_result" not in st.session_state:
    st.session_state.scan_result = None
if "params" not in st.session_state:
    st.session_state.params = {"top": TOP_N, "days": 120}
if "chart_cache" not in st.session_state:
    st.session_state.chart_cache = {}


def run_scan_btn(top: int, days: int, force: bool) -> None:
    """执行扫描并存入 session_state"""
    import time
    import run_screener

    with st.spinner(f"全市场扫描中…（{days}日K线 + 资金流 + 基本面，约2-3分钟）"):
        try:
            result = run_screener.run_scan(top=top, days=days, force=force)
            st.session_state.scan_result = result
            st.session_state.chart_cache = {}
        except Exception as e:  # noqa: BLE001
            st.error(f"扫描失败: {e}")


def load_latest_xlsx() -> pd.DataFrame | None:
    """加载最新一次扫描的 Excel（若 session 无结果）"""
    files = sorted(Path(OUT_DIR).glob("accumulation_breakout_top*.xlsx"))
    if not files:
        return None
    return pd.read_excel(files[-1], dtype={"代码": str})


def _gen_chart_on_demand(code: str, days: int, df: pd.DataFrame) -> Path | None:
    """按需生成单只股票K线图（优先用会话内数据，否则读缓存）"""
    try:
        import data_fetch  # noqa: F401
        import charting
        from signals import detect_accumulation_breakout

        result = st.session_state.scan_result
        if result and "kline_dfs" in result and code in result["kline_dfs"]:
            kdf = result["kline_dfs"][code]
            sig = result["sig"].get(code, {})
        else:
            # 从缓存读取
            import pickle
            cache_files = sorted(Path(OUT_DIR).glob("cache/market_*.pkl"))
            if not cache_files:
                return None
            with open(cache_files[-1], "rb") as f:
                basic, trade_dates, daily, dbbasic, mf = pickle.load(f)
            g = daily[daily["ts_code"] == code].sort_values("trade_date").copy()
            g["date"] = pd.to_datetime(g["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            kdf = g
            sig = detect_accumulation_breakout(g)
        name = df[df["ts_code"] == code]["名称"].iloc[0] if len(df[df["ts_code"] == code]) else code
        p = charting.plot_kline(kdf, code, name, sig, out_dir=OUT_DIR / "charts")
        return Path(p)
    except Exception as e:  # noqa: BLE001
        st.warning(f"K线图生成失败: {e}")
        return None


# ── 侧边栏 ──
with st.sidebar:
    st.title("⚙️ 参数配置")
    top_n = st.number_input("输出数量 Top N", min_value=5, max_value=100, value=st.session_state.params["top"], step=5)
    days = st.number_input("回看天数", min_value=60, max_value=250, value=st.session_state.params["days"], step=10)
    force = st.checkbox("强制重新拉取数据", value=False,
                        help="勾选后忽略本地缓存，重新从 Tushare 拉取全市场K线/资金流")
    run_btn = st.button("🚀 运行选股扫描", type="primary", width="stretch")
    if run_btn:
        st.session_state.params = {"top": int(top_n), "days": int(days)}
        run_scan_btn(int(top_n), int(days), force)
        st.rerun()

    st.divider()
    st.caption("数据源：Tushare HTTP 直连（a.sszhixia.cn）")
    st.caption("策略：1~6个月横盘吸筹 → 放量突破 → 资金流入 → 基本面过滤 + 主题配额")

    # 已有扫描结果显示
    result = st.session_state.scan_result
    if result is None:
        xlsx_df = load_latest_xlsx()
        if xlsx_df is not None:
            st.success(f"显示上次扫描结果（{len(xlsx_df)} 只）")
            st.caption("点击上方按钮重新扫描")

# ── 主界面 ──
st.title("📈 A股 横盘吸筹 → 启动 选股系统")
st.caption("识别横盘吸筹完成后的启动行情：技术形态 + 资金流 + 基本面 三层筛选")

result = st.session_state.scan_result
if result is not None and "df" in result:
    df = result["df"]
    latest_date = result.get("latest_date", "-")
    elapsed = result.get("elapsed_sec", 0)
    total_cand = result.get("total_candidates", len(df))
    hits = len(result.get("hits", []))
else:
    df = load_latest_xlsx()
    latest_date = Path(OUT_DIR).glob("accumulation_breakout_top*.xlsx")
    latest_date = sorted(latest_date)[-1].stem.split("_")[-1] if latest_date else "-"
    hits, total_cand, elapsed = None, None, None

if df is None or df.empty:
    st.warning("暂无扫描结果。请在左侧配置参数后点击「运行选股扫描」。")
    st.stop()

# ── 顶部指标 ──
c1, c2, c3, c4 = st.columns(4)
c1.metric("最新交易日", latest_date)
c2.metric("信号命中", f"{hits} 只" if hits is not None else "-")
c3.metric("最终候选", f"{len(df)} 只")
c4.metric("扫描耗时", f"{elapsed:.0f}s" if elapsed else "-")

tab1, tab2, tab3, tab4 = st.tabs(["📋 选股结果", "📊 K线图浏览器", "💰 资金流明细", "📄 策略报告"])

# ══════════════ Tab1: 选股结果 ══════════════
with tab1:
    st.subheader(f"Top {len(df)} 候选（按综合分排序）")
    display_cols = ["代码", "名称", "最新价", "行业", "总市值(亿)", "PE(TTM)", "PB",
                    "换手率%", "箱体天数", "箱体振幅%", "量比", "突破日涨幅%",
                    "主力净流入(万)", "净流入/成交额%", "综合分", "入选理由"]
    show = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[show].style.format({
            "最新价": "{:.2f}", "总市值(亿)": "{:.1f}", "PE(TTM)": "{:.1f}", "PB": "{:.2f}",
            "换手率%": "{:.2f}", "箱体振幅%": "{:.1f}", "量比": "{:.2f}",
            "突破日涨幅%": "{:.2f}", "主力净流入(万)": "{:,.0f}",
            "净流入/成交额%": "{:.2f}", "综合分": "{:.1f}",
        }),
        height=560, width="stretch",
        column_config={"代码": st.column_config.TextColumn("代码", width="small")},
    )

    st.download_button(
        "⬇️ 下载 Excel",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"accumulation_breakout_top{len(df)}_{latest_date}.csv",
        mime="text/csv",
        width="stretch",
    )

# ══════════════ Tab2: K线图浏览器 ══════════════
with tab2:
    st.subheader("K线图浏览器（标注箱体与突破点）")
    codes = df["ts_code"].tolist()
    names = df["名称"].tolist()
    labels = [f"{n}（{c}）" for n, c in zip(names, codes)]
    sel = st.selectbox("选择股票", labels, index=0)
    sel_idx = labels.index(sel)
    sel_code = codes[sel_idx]

    # 优先用已生成的图，否则重新生成
    chart_path = None
    if "K线图" in df.columns and pd.notna(df.iloc[sel_idx]["K线图"]):
        p = Path(str(df.iloc[sel_idx]["K线图"]))
        if p.exists():
            chart_path = p
    if chart_path is None:
        chart_path = _gen_chart_on_demand(sel_code, days, df)

    if chart_path:
        st.image(str(chart_path), width="stretch")
        with open(chart_path, "rb") as f:
            st.download_button("⬇️ 下载此K线图", data=f.read(),
                               file_name=Path(chart_path).name, mime="image/png")
    else:
        st.info("该股票K线图不可用")

    # 信号详情
    row = df.iloc[sel_idx]
    st.markdown("**信号详情**")
    det_cols = ["箱体天数", "箱体振幅%", "量比", "突破日涨幅%", "主力净流入(万)", "净流入/成交额%", "综合分", "入选理由", "突破日"]
    det = {c: row[c] for c in det_cols if c in df.columns}
    st.json({k: (f"{v:.2f}" if isinstance(v, float) else str(v)) for k, v in det.items()})

# ══════════════ Tab3: 资金流明细 ══════════════
with tab3:
    st.subheader("近5日主力资金流（超大单+大单）")
    flow_cols = ["代码", "名称", "主力净流入(万)", "净流入/成交额%", "最新价", "综合分"]
    flow_show = [c for c in flow_cols if c in df.columns]
    st.dataframe(
        df[flow_show].sort_values("主力净流入(万)", ascending=False).style.format({
            "主力净流入(万)": "{:,.0f}", "净流入/成交额%": "{:.2f}",
            "最新价": "{:.2f}", "综合分": "{:.1f}",
        }),
        height=560, width="stretch",
    )
    st.caption("主力资金 = 超大单 + 大单净流入；净流入/成交额 衡量资金强度")

# ══════════════ Tab4: 策略报告 ══════════════
with tab4:
    st.subheader("策略说明与报告")
    report_files = sorted(Path(OUT_DIR).glob(f"accumulation_breakout_report_{latest_date}.md"))
    if report_files:
        with open(report_files[-1], encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.markdown("""
**策略逻辑（三层筛选）**
1. **技术形态**：1~6个月箱体横盘（约20~125交易日，振幅≤28%、趋势平坦）+ 最近5日内放量突破箱体上沿（量比≥1.6倍、涨幅2%-9.5%）
2. **资金流确认**：近5日主力（超大单+大单）净流入为正
3. **基本面过滤**：非ST/退市/次新，PE≤60、PB≤12、市值30-3000亿

**综合评分**：信号强度55%（横盘越长+信号越明确越高）+ 资金流25% + 基本面20%
**风险提示**：技术信号筛选不构成投资建议；假突破风险需配合止损（跌破箱体上沿/MA20）
""")

if __name__ == "__main__":
    pass
