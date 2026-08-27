"""Trusted Lab report assembly and Markdown rendering."""

from __future__ import annotations

import json
from typing import Any

_PARAM_KEYS = ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")


def _combo_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in _PARAM_KEYS)


def freeze_is_winner(is_rows: list[dict[str, Any]], oos_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze IS rank one; OOS performance is never used to choose a replacement."""
    if not is_rows:
        return {"primary_is": None, "primary_oos": None, "sensitivity": []}
    primary_is = dict(is_rows[0])
    by_param = {str(row.get("param_id")): row for row in oos_rows if row.get("param_id")}
    by_combo = {_combo_key(row): row for row in oos_rows}

    def matching_oos(row: dict[str, Any]) -> dict[str, Any] | None:
        match = by_param.get(str(row.get("param_id"))) if row.get("param_id") else None
        return dict(match or by_combo.get(_combo_key(row)) or {}) or None

    primary_oos = matching_oos(primary_is)
    sensitivity: list[dict[str, Any]] = []
    for row in is_rows[1:3]:
        combined = dict(row)
        combined.update(matching_oos(row) or {})
        sensitivity.append(combined)
    return {
        "primary_is": primary_is,
        "primary_oos": primary_oos,
        "sensitivity": sensitivity,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def render_trusted_report(report: dict[str, Any]) -> str:
    """Render a compact, self-contained report suitable for download/audit."""
    verdict = report.get("verdict") or "INSUFFICIENT_EVIDENCE"
    versions = report.get("versions") or {}
    sample = report.get("sample") or {}
    costs = report.get("cost_assumptions") or {}
    checks = report.get("checks") or []
    lines = [
        f"# 策略实验室可信报告 — {verdict}",
        "",
        "## 结论",
        "",
        str(report.get("summary") or "证据不足，不能形成候选参数结论。"),
        "",
    ]
    for reason in report.get("block_reasons") or []:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "> PASS 也只表示允许登记为隔离候选参数；不会自动进入 A 池或生成订单。",
            "",
            "## 样本与版本",
            "",
            f"- 研究运行：`{report.get('research_run_id')}`",
            f"- 股票池数量：{sample.get('universe_size')}",
            f"- 窗口：`{_json(sample.get('windows'))}`",
            f"- 数据版本：`{versions.get('dataset')}`",
            f"- 代码版本：`{versions.get('code')}`",
            f"- 成本版本：`{versions.get('cost')}`",
            f"- 组合账户版本：`{_json(report.get('portfolio_model'))}`",
            "",
            "## 成本口径",
            "",
            f"`{_json(costs)}`",
            "",
            "## 组合记账口径",
            "",
            "共享现金、重叠持仓、整数分/微元费用、每日收盘盯市；"
            "`net_avg_return` 为兼容字段，含义是窗口组合净总收益。",
            f"`{_json(report.get('portfolio_model'))}`",
            "",
            "## IS / OOS",
            "",
            f"- 冻结的 IS 第一名：`{_json(report.get('primary_is'))}`",
            f"- 对应 OOS：`{_json(report.get('primary_oos'))}`",
            "",
            "## Walk-forward",
            "",
            f"`{_json(report.get('wf_windows') or [])}`",
            "",
            "## 基线对照",
            "",
            f"- 固定种子随机：`{_json((report.get('baselines') or {}).get('random'))}`",
            f"- MA20/60：`{_json((report.get('baselines') or {}).get('ma20_60'))}`",
            "",
            "## 反过拟合",
            "",
            f"`{_json(report.get('anti_overfit'))}`",
            "",
            "## 多重比较披露",
            "",
            f"`{_json(report.get('multiple_comparison') or {})}`",
            "",
            "## 门禁检查",
            "",
        ]
    )
    for check in checks:
        marker = "x" if check.get("passed") else " "
        lines.append(
            f"- [{marker}] {check.get('label')}；实际 `{_json(check.get('actual'))}`；要求 {check.get('threshold')}"
        )
    lines.extend(
        [
            "",
            "## IS 第二/三名敏感性",
            "",
            f"`{_json(report.get('sensitivity') or [])}`",
            "",
            "---",
            "本报告用于个人研究纪律与复现，不构成投资建议。",
        ]
    )
    return "\n".join(lines) + "\n"
