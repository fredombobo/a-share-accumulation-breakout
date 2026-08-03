"""
生成 Markdown 报告
==================
读取 run_screener 输出的 Excel，生成完整研究报告（含资金流佐证、风险提示）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import OUT_DIR  # noqa: E402


def _md_escape(v) -> str:
    """Markdown 转义：| 和换行会破坏表格，做安全替换。"""
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _cell(r: pd.Series, col: str, default: str = "-", fmt=None) -> str:
    """安全取单元格：列不存在或值为空时返回默认值，避免缺列 KeyError。"""
    if col not in r.index:
        return default
    v = r[col]
    if v is None or pd.isna(v):
        return default
    if fmt is not None:
        try:
            return _md_escape(fmt(v))
        except (TypeError, ValueError):
            return default
    return _md_escape(v)


def generate_report(xlsx_path: str, latest_date: str) -> str:
    df = pd.read_excel(xlsx_path)
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str)

    lines = []
    lines.append(f"# A股 横盘吸筹→启动 选股报告（{latest_date}）\n")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append(f"> 数据源：Tushare HTTP 直连（a.sszhixia.cn）｜筛选样本：全市场 {len(df)} 只（Top）  \n")

    lines.append("## 一、策略说明\n")
    lines.append("本报告识别**横盘吸筹完成后启动行情**的股票，三层筛选：\n")
    lines.append("1. **技术形态**：1~6个月箱体横盘（约20~125交易日，振幅≤28%、趋势平坦）+ 最近5日内放量突破箱体上沿（量比≥1.6倍、涨幅2%-9.5%）")
    lines.append("2. **资金流确认**：近5日主力（超大单+大单）净流入为正")
    lines.append("3. **基本面过滤**：非ST/退市/次新，PE≤60、PB≤12、市值30-3000亿\n")

    lines.append("## 二、主题板块配额\n")
    lines.append("强制覆盖：AI应用 / 半导体 / 光模块 / 机器人 / 电力 / 芯片，**每板块至少 5 只**；总输出目标 **50 只**。\n")
    if "主题板块" in df.columns:
        vc = df["主题板块"].value_counts()
        lines.append("| 主题板块 | 数量 |")
        lines.append("|----------|------|")
        for k, v in vc.items():
            lines.append(f"| {_md_escape(k)} | {v} |")
        lines.append("")

    lines.append(f"## 三、Top {len(df)} 候选\n")
    # 按实际存在的列生成表头，缺列自动跳过，避免 KeyError
    col_order = [
        ("代码", None),
        ("名称", None),
        ("主题板块", None),
        ("最新价", lambda v: f"{float(v):.2f}"),
        ("行业", None),
        ("总市值(亿)", lambda v: f"{float(v):.0f}"),
        ("PE(TTM)", lambda v: f"{float(v):.1f}"),
        ("PB", lambda v: f"{float(v):.2f}"),
        ("综合分", lambda v: f"{float(v):.1f}"),
        ("主力净流入(万)", lambda v: f"{float(v):.0f}"),
        ("突破日", None),
    ]
    present = [c for c, _ in col_order if c in df.columns]
    lines.append("| # | " + " | ".join(present) + " |")
    lines.append("|---|" + "|".join(["---"] * len(present)) + "|")
    for i, r in df.iterrows():
        cells = [str(i + 1)]
        for c, fmt in col_order:
            if c not in present:
                continue
            default = "亏损" if c == "PE(TTM)" else "-"
            cells.append(_cell(r, c, default=default, fmt=fmt))
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n## 四、入选理由摘录\n")
    for i, r in df.head(12).iterrows():
        theme = f"[{_cell(r, '主题板块', default='')}] " if "主题板块" in df.columns else ""
        lines.append(
            f"- **{theme}{_cell(r, '名称')}（{_cell(r, '代码')}）**：{_cell(r, '入选理由', default='')}；"
            f"主力净流入 {_cell(r, '主力净流入(万)', fmt=lambda v: f'{float(v):.0f}')} 万元"
        )

    lines.append("\n## 五、资金流佐证\n")
    if "主力净流入(万)" in df.columns and len(df):
        lines.append("资金流最强的前5：")
        top_flow = df.nlargest(5, "主力净流入(万)")
        for i, r in top_flow.iterrows():
            ratio = _cell(r, "净流入/成交额%", default="-", fmt=lambda v: f"{float(v):.2f}%")
            lines.append(f"- **{_cell(r, '名称')}**：净流入 {_cell(r, '主力净流入(万)', fmt=lambda v: f'{float(v):.0f}')} 万元，占成交额 {ratio}")

    lines.append("\n## 六、风险提示\n")
    lines.append("- 本报告为**量化技术信号 + 资金流 + 基本面 + 主题配额**的自动化筛选，不构成投资建议")
    lines.append("- 为满足主题覆盖，部分标的可能来自「放宽补齐」层（参数略松），见「筛选层级」列")
    lines.append("- 突破信号存在假突破风险，建议结合止损（跌破箱体上沿/MA20）管理")
    lines.append("- 数据截至交易日收盘；资金流为 Tushare 口径（超大单+大单）")
    lines.append("- 市场整体环境（牛熊）对突破成功率影响显著，弱市中应降低仓位")

    report_path = os.path.join(OUT_DIR, f"accumulation_breakout_report_{latest_date}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path


if __name__ == "__main__":
    import glob
    xlsx_files = sorted(glob.glob(os.path.join(OUT_DIR, "accumulation_breakout_top*.xlsx")))
    if not xlsx_files:
        print("未找到 Excel 输出，请先运行 run_screener.py")
        sys.exit(1)
    latest_xlsx = xlsx_files[-1]
    latest_date = os.path.basename(latest_xlsx).split("_")[-1].replace(".xlsx", "")
    path = generate_report(latest_xlsx, latest_date)
    print("报告已生成:", path)
