"""策略评分标尺 v0 的机器裁决（docs/STRATEGY-SCORECARD-V0.md）。

把「2→9 分」变成可复算的命令：分数只由证据文件决定，文档里的自评不参与。

证据目录（默认 `runtime/research/scorecard/`，运行证据，不入库）::

    system/G0.json                      系统级数据与测量门禁
    <hypothesis_id>/registration.json   预登记（冻结时间、文档哈希、试验上限）
    <hypothesis_id>/trials.json         试验台账
    <hypothesis_id>/G2.json … G4.json   单机制门禁
    portfolio/portfolio.json            组合成员（≥2 个已达 6 分的假设）
    portfolio/G5.json G6.json G7.json G8.json

每个门禁文件的公共字段::

    {"gate": "G3", "generated_at": "<ISO8601+08:00>",
     "artifacts": {"<相对证据根的路径>": "<sha256>"},   # 至少 1 个，逐个复核
     "metrics": {...}}

防伪规则（任一不满足 → INVALID，不计分）：
- artifacts 必须非空、文件存在且 SHA-256 一致；
- 假设级门禁（G2–G4）的 generated_at 必须晚于预登记 frozen_at；
- 预登记文档若给出仓库路径，其当前 SHA-256 必须等于登记值（冻结后不得修改）。

分数映射（与标尺一致，只升不降，除非出现反证）：
- G0 通过 → 4；否则 2
- 任一假设 G0–G4 通过 → 6
- 组合：≥2 个 6 分假设 + G5、G8 通过 + 组合净 Sharpe ≥ 0.8 → 8
- 再 G6、G7 通过 + 3× 成本仍为正 + 前瞻 ≥12 个月 + 未衰减 → 9
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

PASS = "PASS"
FAIL = "FAIL"
KILLED = "KILLED"
MISSING = "MISSING"
INVALID = "INVALID"

HYPOTHESIS_GATES = ("G2", "G3", "G4")
PORTFOLIO_GATES = ("G5", "G6", "G7", "G8")
MAX_TRIALS_CAP = 5
# 「2–3 个弱相关机制」的 v0 操作化：成员两两日收益相关 ≤ 0.5
MAX_MEMBER_CORRELATION = 0.5


@dataclass
class GateResult:
    gate: str
    status: str
    reasons: list[str] = field(default_factory=list)
    evidence_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "reasons": self.reasons,
            "evidence_sha256": self.evidence_sha256,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_TZ)


def _num(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(metrics: dict[str, Any], key: str) -> bool:
    return metrics.get(key) is True


def _load_gate(root: Path, path: Path, gate: str) -> tuple[dict[str, Any] | None, GateResult | None]:
    if not path.is_file():
        return None, GateResult(gate, MISSING, [f"缺少证据文件 {path.relative_to(root)}"])
    digest = sha256_file(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, GateResult(gate, INVALID, [f"证据不是合法 JSON: {exc}"], digest)
    problems: list[str] = []
    if doc.get("gate") != gate:
        problems.append(f"gate 字段应为 {gate}")
    if _parse_time(doc.get("generated_at")) is None:
        problems.append("generated_at 缺失或不可解析")
    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        problems.append("artifacts 为空：门禁必须引用可复算工件")
    else:
        base = root.resolve()
        for rel, expected in artifacts.items():
            target = (root / rel).resolve()
            if not target.is_relative_to(base):
                problems.append(f"工件路径越界: {rel}")
            elif not target.is_file():
                problems.append(f"工件不存在: {rel}")
            elif sha256_file(target) != str(expected):
                problems.append(f"工件哈希不符: {rel}")
    if not isinstance(doc.get("metrics"), dict):
        problems.append("metrics 缺失")
    if problems:
        return None, GateResult(gate, INVALID, problems, digest)
    doc["_sha256"] = digest
    return doc, None


def _judge(gate: str, doc: dict[str, Any], reasons: list[str], kills: list[str]) -> GateResult:
    if kills:
        return GateResult(gate, KILLED, kills + reasons, doc["_sha256"])
    return GateResult(gate, FAIL if reasons else PASS, reasons, doc["_sha256"])


# ── 各门禁判定（阈值取自标尺第 1、3 节，不得在此放宽）──


def judge_g0(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    required = {
        "pit_history_complete": "全历史 PIT 可用时点",
        "delisted_covered": "退市股覆盖",
        "suspension_limit_modeled": "停牌/涨跌停可成交性",
        "adjustment_complete": "复权完整",
        "cost_model_calibrated": "成本与冲击模型校准",
        "manifest_reproducible": "行情分区 manifest 可复算",
    }
    reasons = [f"未满足：{label}" for key, label in required.items() if not _flag(m, key)]
    return _judge("G0", doc, reasons, [])


def judge_g2(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons: list[str] = []
    kills: list[str] = []
    n, lo, mean, placebo = (_num(m, k) for k in ("n", "ci_lo", "mean", "placebo_mean"))
    if n is None or lo is None or mean is None or placebo is None:
        return GateResult("G2", INVALID, ["metrics 需含 n / ci_lo / mean / placebo_mean"], doc["_sha256"])
    if lo <= 0 and mean <= 0:
        kills.append(f"止损 1：95% CI 下界 {lo:.4f} ≤ 0 且点估计 {mean:.4f} ≤ 0")
    if n < 300:
        reasons.append(f"样本 {n:.0f} < 300")
    if lo <= 0:
        reasons.append(f"聚类 95% CI 下界 {lo:.4f} ≤ 0")
    if mean <= placebo:
        reasons.append(f"均值 {mean:.4f} 未优于伪事件 {placebo:.4f}")
    return _judge("G2", doc, reasons, kills)


def judge_g3(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons: list[str] = []
    kills: list[str] = []
    years, sharpe, trades = (_num(m, k) for k in ("oos_years", "oos_net_sharpe", "oos_trades"))
    if years is None or sharpe is None or trades is None:
        return GateResult("G3", INVALID, ["metrics 需含 oos_years / oos_net_sharpe / oos_trades"], doc["_sha256"])
    if sharpe < 0.3:
        kills.append(f"止损 2：OOS 净 Sharpe {sharpe:.2f} < 0.3")
    if trades < 100:
        kills.append(f"止损 2：OOS 独立交易 {trades:.0f} < 100")
    if years < 2:
        reasons.append(f"OOS {years:.1f} 年 < 2 年")
    if not _flag(m, "includes_bear"):
        reasons.append("OOS 未含熊市段")
    if m.get("oos_viewed_during_development") is not False:
        reasons.append("未证明 OOS 在开发期未被查看（oos_viewed_during_development 必须为 false）")
    return _judge("G3", doc, reasons, kills)


def judge_g4(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons: list[str] = []
    kills: list[str] = []
    pbo, dsr, mintrl = (_num(m, k) for k in ("pbo", "dsr", "mintrl_coverage"))
    if pbo is None or dsr is None or mintrl is None:
        return GateResult("G4", INVALID, ["metrics 需含 pbo / dsr / mintrl_coverage"], doc["_sha256"])
    if pbo > 0.20:
        kills.append(f"止损 3：PBO {pbo:.2%} > 20%")
    if m.get("perturb_20pct_sign_flip") is not False:
        kills.append("止损 3：参数 ±20% 扰动符号翻转（或未报告）")
    if pbo >= 0.10:
        reasons.append(f"PBO {pbo:.2%} ≥ 10%")
    if dsr < 0.95:
        reasons.append(f"DSR {dsr:.2f} < 0.95")
    if mintrl < 1:
        reasons.append(f"MinTRL 覆盖 {mintrl:.2f} < 1")
    if not _flag(m, "drop_best_5pct_still_positive"):
        reasons.append("去掉最好 5% 交易后不再为正（或未报告）")
    return _judge("G4", doc, reasons, kills)


def judge_g5(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons: list[str] = []
    kills: list[str] = []
    c2, c3, cap = (_num(m, k) for k in ("cost_2x_expectancy", "cost_3x_expectancy", "capacity_multiple"))
    if c2 is None or c3 is None or cap is None:
        return GateResult(
            "G5", INVALID, ["metrics 需含 cost_2x_expectancy / cost_3x_expectancy / capacity_multiple"],
            doc["_sha256"],
        )
    if c2 <= 0:
        kills.append(f"止损 4：2× 成本期望 {c2:.4f} ≤ 0")
    if c3 < 0:
        reasons.append(f"3× 成本期望 {c3:.4f} < 0")
    if cap < 5:
        reasons.append(f"容量为目标资金 {cap:.1f} 倍 < 5 倍")
    if _num(m, "adv_participation_cap") is None:
        reasons.append("未给出 ADV 参与率上限")
    return _judge("G5", doc, reasons, kills)


def judge_g6(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons: list[str] = []
    kills: list[str] = []
    months, slip, fill = (_num(m, k) for k in ("shadow_months", "slippage_deviation", "fill_rate_deviation"))
    if months is None or slip is None or fill is None:
        return GateResult(
            "G6", INVALID, ["metrics 需含 shadow_months / slippage_deviation / fill_rate_deviation"],
            doc["_sha256"],
        )
    if slip > 0.30 or fill > 0.30:
        kills.append(f"止损 5：前瞻偏差 滑点 {slip:.0%} / 成交率 {fill:.0%} > 30%")
    if months < 6:
        reasons.append(f"Shadow {months:.1f} 个月 < 6 个月")
    if not _flag(m, "limit_unfillable_recorded"):
        reasons.append("未如实记录涨跌停不可成交")
    return _judge("G6", doc, reasons, kills)


def judge_g7(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    reasons = []
    if not _flag(m, "replicated_other_source_or_regime"):
        reasons.append("未在换数据源/市场/制度阶段后复现")
    if not _flag(m, "independent_implementation_passed"):
        reasons.append("未经不同实现复算")
    return _judge("G7", doc, reasons, [])


def judge_g8(doc: dict[str, Any]) -> GateResult:
    m = doc["metrics"]
    required = {
        "vol_target": "波动目标",
        "neutralization": "行业/市值中性化",
        "correlation_constraint": "相关性约束",
        "drawdown_budget": "回撤与尾部预算",
        "industry_exposure_budget": "行业暴露预算",
    }
    reasons = [f"未满足：{label}" for key, label in required.items() if not _flag(m, key)]
    return _judge("G8", doc, reasons, [])


_JUDGES = {
    "G0": judge_g0, "G2": judge_g2, "G3": judge_g3, "G4": judge_g4,
    "G5": judge_g5, "G6": judge_g6, "G7": judge_g7, "G8": judge_g8,
}


_NOT_FROZEN = object()


def _run_gate(root: Path, path: Path, gate: str, *, frozen: Any = None) -> GateResult:
    """加载 + 防伪校验 + 阈值判定。frozen 非 None 时要求证据晚于预登记冻结时间。"""
    doc, problem = _load_gate(root, path, gate)
    if problem is not None:
        return problem
    if doc is None:
        return GateResult(gate, INVALID, ["证据加载失败"])
    if frozen is not None:
        generated = _parse_time(doc["generated_at"])
        if frozen is _NOT_FROZEN or generated is None or generated <= frozen:
            return GateResult(
                gate, INVALID, ["证据生成时间不晚于预登记冻结时间（或预登记无效）"], doc["_sha256"]
            )
    return _JUDGES[gate](doc)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def judge_registration(hyp_dir: Path, repo_root: Path) -> tuple[GateResult, datetime | None]:
    path = hyp_dir / "registration.json"
    if not path.is_file():
        return GateResult("G1", MISSING, ["缺少 registration.json（未预登记的探索不得晋级）"]), None
    digest = sha256_file(path)
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return GateResult("G1", INVALID, [f"registration.json 非法: {exc}"], digest), None
    reasons: list[str] = []
    frozen = _parse_time(reg.get("frozen_at"))
    if frozen is None:
        reasons.append("frozen_at 缺失或不可解析")
    if reg.get("hypothesis_id") != hyp_dir.name:
        reasons.append("hypothesis_id 与目录名不一致")
    if not str(reg.get("mechanism") or "").strip():
        reasons.append("缺少机制说明")
    doc_path, doc_sha = reg.get("document"), reg.get("document_sha256")
    if not doc_path or not doc_sha:
        reasons.append("缺少预登记文档路径或 SHA-256")
    else:
        target = (repo_root / str(doc_path)).resolve()
        if not target.is_file():
            reasons.append(f"预登记文档不存在: {doc_path}")
        elif sha256_file(target) != doc_sha:
            reasons.append("预登记文档在冻结后被修改（SHA-256 不符）")
    max_trials = reg.get("max_trials")
    if not isinstance(max_trials, int) or not 1 <= max_trials <= MAX_TRIALS_CAP:
        reasons.append(f"max_trials 必须是 1–{MAX_TRIALS_CAP} 的整数")
    if reasons:
        return GateResult("G1", INVALID, reasons, digest), None
    trials_path = hyp_dir / "trials.json"
    trials: list[Any] = []
    if trials_path.is_file():
        try:
            trials = list(json.loads(trials_path.read_text(encoding="utf-8")).get("trials") or [])
        except (json.JSONDecodeError, AttributeError):
            return GateResult("G1", INVALID, ["trials.json 非法"], digest), frozen
    if len(trials) > int(max_trials):
        return GateResult(
            "G1", KILLED, [f"止损 6：试验 {len(trials)} 次超过预登记上限 {max_trials}"], digest
        ), frozen
    return GateResult("G1", PASS, [], digest), frozen


def evaluate_hypothesis(root: Path, hyp_dir: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    g1, frozen = judge_registration(hyp_dir, repo_root or _repo_root())
    gates: list[GateResult] = [g1]
    for gate in HYPOTHESIS_GATES:
        gates.append(
            _run_gate(root, hyp_dir / f"{gate}.json", gate, frozen=frozen if frozen else _NOT_FROZEN)
        )
    killed = [g.gate for g in gates if g.status == KILLED]
    return {
        "hypothesis_id": hyp_dir.name,
        "status": "KILLED" if killed else ("SINGLE_MECHANISM_PASS" if all(g.status == PASS for g in gates) else "OPEN"),
        "killed_by": killed,
        "gates": [g.as_dict() for g in gates],
    }


def _portfolio(root: Path, six_point: set[str]) -> dict[str, Any] | None:
    pdir = root / "portfolio"
    spec_path = pdir / "portfolio.json"
    if not spec_path.is_file():
        return None
    reasons: list[str] = []
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": INVALID, "reasons": [f"portfolio.json 非法: {exc}"], "gates": []}
    members = [str(m) for m in spec.get("members") or []]
    not_ready = [m for m in members if m not in six_point]
    if len(members) < 2:
        reasons.append("组合成员少于 2 个")
    if not_ready:
        reasons.append(f"成员未达 6 分: {not_ready}")
    corr = spec.get("max_pairwise_correlation")
    if not isinstance(corr, (int, float)) or corr > MAX_MEMBER_CORRELATION:
        reasons.append(f"成员两两相关须 ≤ {MAX_MEMBER_CORRELATION}（或未报告）")
    gates: dict[str, GateResult] = {}
    for gate in PORTFOLIO_GATES:
        gates[gate] = _run_gate(root, pdir / f"{gate}.json", gate)
    return {"members": members, "reasons": reasons, "gates": gates, "spec": spec}


def evaluate(root: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    """扫描证据目录，返回完整裁决与分数。"""
    root = root.resolve()
    g0 = _run_gate(root, root / "system" / "G0.json", "G0")

    hypotheses = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in {"system", "portfolio"}:
                hypotheses.append(evaluate_hypothesis(root, child, repo_root=repo_root))
    six_point = {h["hypothesis_id"] for h in hypotheses if h["status"] == "SINGLE_MECHANISM_PASS"}

    score = 2
    blockers: list[str] = []
    portfolio_report: dict[str, Any] | None = None
    if g0.status != PASS:
        blockers.append("G0 未通过：" + "；".join(g0.reasons))
    else:
        score = 4
        if not six_point:
            blockers.append("没有任何预登记假设通过 G1–G4")
        else:
            score = 6
            port = _portfolio(root, six_point)
            if port is None:
                blockers.append("缺少组合层证据 portfolio/portfolio.json")
            else:
                g = port["gates"]
                sharpe = port["spec"].get("portfolio_net_sharpe")
                eight_reasons = list(port["reasons"])
                for gate in ("G5", "G8"):
                    if g[gate].status != PASS:
                        eight_reasons.append(f"{gate} {g[gate].status}")
                if not isinstance(sharpe, (int, float)) or sharpe < 0.8:
                    eight_reasons.append("组合净 Sharpe < 0.8（或未报告）")
                if eight_reasons:
                    blockers.extend(eight_reasons)
                else:
                    score = 8
                    nine_reasons = [f"{gate} {g[gate].status}" for gate in ("G6", "G7") if g[gate].status != PASS]
                    spec = port["spec"]
                    c3 = spec.get("portfolio_cost_3x_expectancy")
                    if not isinstance(c3, (int, float)) or c3 <= 0:
                        nine_reasons.append("3× 成本组合期望未为正")
                    months = spec.get("forward_live_months")
                    if not isinstance(months, (int, float)) or months < 12:
                        nine_reasons.append("前瞻一致记录不足 12 个月")
                    if spec.get("decay_detected") is not False:
                        nine_reasons.append("未证明无衰减（decay_detected 必须为 false）")
                    if nine_reasons:
                        blockers.extend(nine_reasons)
                    else:
                        score = 9
            if port is not None:
                port["gates"] = [r.as_dict() for r in port["gates"].values()]
                port.pop("spec", None)
            portfolio_report = port
    result: dict[str, Any] = {
        "scorecard_version": "v0",
        "evaluated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "evidence_root": str(root),
        "score": score,
        "next_blockers": blockers,
        "system": {"G0": g0.as_dict()},
        "hypotheses": hypotheses,
    }
    if score >= 6:
        result["portfolio"] = portfolio_report
    return result


def g2_from_event_study(study_dir: Path, evidence_root: Path, *, horizon: int = 20) -> dict[str, Any]:
    """把 scripts/event_study_matched.py 的产物转成 G2 证据（工件哈希逐个登记）。"""
    manifest_path = study_dir / "manifest.json"
    stats_path = study_dir / "stats.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    event = stats["event_diff"][str(horizon)]
    placebo = stats["placebo_diff"].get(str(horizon), {})
    root = evidence_root.resolve()
    artifacts: dict[str, str] = {}
    for path in (manifest_path, stats_path, study_dir / "diffs.json"):
        if path.is_file():
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError(f"事件研究产物必须位于证据根目录内: {resolved}")
            artifacts[str(resolved.relative_to(root))] = sha256_file(resolved)
    return {
        "gate": "G2",
        "generated_at": manifest["generated_at"],
        "source": "scripts/event_study_matched.py",
        "study_verdict": manifest.get("verdict"),
        "horizon": horizon,
        "artifacts": artifacts,
        "metrics": {
            "n": event.get("n"),
            "mean": event.get("mean"),
            "ci_lo": event.get("lo"),
            "ci_hi": event.get("hi"),
            "placebo_mean": placebo.get("mean"),
        },
    }
