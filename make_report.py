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


def generate_report(xlsx_path: str, latest_date: str) -> str:
    df = pd.read_excel(xlsx_path, dtype={"代码": str})

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
            lines.append(f"| {k} | {v} |")
        lines.append("")

    lines.append(f"## 三、Top {len(df)} 候选\n")
    has_theme = "主题板块" in df.columns
    if has_theme:
        lines.append("| # | 代码 | 名称 | 主题 | 最新价 | 行业 | 总市值(亿) | PE(TTM) | PB | 综合分 | 主力净流入(万) | 突破日 |")
        lines.append("|---|------|------|------|--------|------|-----------|---------|----|--------|---------------|--------|")
    else:
        lines.append("| # | 代码 | 名称 | 最新价 | 行业 | 总市值(亿) | PE(TTM) | PB | 综合分 | 主力净流入(万) | 突破日 |")
        lines.append("|---|------|------|--------|------|-----------|---------|----|--------|---------------|--------|")
    for i, r in df.iterrows():
        pe = f"{r['PE(TTM)']:.1f}" if pd.notna(r.get("PE(TTM)")) else "亏损"
        pb = f"{r['PB']:.2f}" if pd.notna(r.get("PB")) else "-"
        mv = f"{r['总市值(亿)']:.0f}" if pd.notna(r.get("总市值(亿)")) else "-"
        theme = r.get("主题板块", "")
        if has_theme:
            lines.append(
                f"| {i+1} | {r['代码']} | {r['名称']} | {theme} | {r['最新价']} | {r['行业']} | "
                f"{mv} | {pe} | {pb} | {r['综合分']:.1f} | "
                f"{r['主力净流入(万)']:.0f} | {r['突破日']} |"
            )
        else:
            lines.append(
                f"| {i+1} | {r['代码']} | {r['名称']} | {r['最新价']} | {r['行业']} | "
                f"{mv} | {pe} | {pb} | {r['综合分']:.1f} | "
                f"{r['主力净流入(万)']:.0f} | {r['突破日']} |"
            )

    lines.append("\n## 四、入选理由摘录\n")
    for i, r in df.head(12).iterrows():
        theme = f"[{r['主题板块']}] " if has_theme and pd.notna(r.get("主题板块")) else ""
        lines.append(
            f"- **{theme}{r['名称']}（{r['代码']}）**：{r['入选理由']}；"
            f"主力净流入 {r['主力净流入(万)']:.0f} 万元"
        )

    lines.append("\n## 五、资金流佐证\n")
    lines.append("资金流最强的前5：")
    top_flow = df.nlargest(5, "主力净流入(万)")
    for i, r in top_flow.iterrows():
        ratio = f"{r['净流入/成交额%']:.2f}%" if pd.notna(r.get("净流入/成交额%")) else "-"
        lines.append(f"- **{r['名称']}**：净流入 {r['主力净流入(万)']:.0f} 万元，占成交额 {ratio}")

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
