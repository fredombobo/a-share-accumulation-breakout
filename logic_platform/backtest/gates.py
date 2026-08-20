"""闸门 gates（docs §6.5 最小可用版）。

基于回测 metrics 自动判定策略状态：
  - 全部规则通过       → status=gated（仍 research_only）
  - 交易次数不足       → status=draft（degraded 标记，禁止上架）
  - 其余规则未过       → status=rejected（列出失败规则）
判定规则可配置（GateConfig），CLI --gate 可覆盖。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 状态流转（docs §7.1 logic_strategies.status）
STATUS_GATED = "gated"
STATUS_REJECTED = "rejected"
STATUS_DRAFT = "draft"


@dataclass
class GateConfig:
    min_trades: int = 30
    max_drawdown: float = 0.35
    min_win_rate: float | None = 0.42
    min_profit_factor: float | None = 1.2
    min_avg_ret: float | None = 0.02

    @classmethod
    def from_dict(cls, d: dict | None) -> GateConfig:
        if not d:
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class GateCheck:
    rule: str
    passed: bool
    actual: float | None
    threshold: float | None
    msg: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class GateResult:
    status: str
    passed: bool
    checks: list[GateCheck] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "passed": self.passed,
            "checks": [c.to_json() for c in self.checks],
        }


def evaluate(metrics: dict, config: GateConfig | None = None) -> GateResult:
    """对回测 metrics 跑闸门。metrics 为 engine.summarize_trades 输出。"""
    cfg = config or GateConfig()
    checks: list[GateCheck] = []
    n = metrics.get("n_trades") or 0

    def _check(rule: str, actual, threshold, cond) -> None:
        passed = bool(cond)
        msg = f"{rule}: {_fmt(actual)} vs 阈值 {_fmt(threshold)}" + (
            " ✅" if passed else " ❌"
        )
        checks.append(GateCheck(rule=rule, passed=passed, actual=actual,
                                threshold=threshold, msg=msg))

    _check("min_trades", float(n), float(cfg.min_trades), n >= cfg.min_trades)
    if cfg.max_drawdown is not None:
        _check("max_drawdown", metrics.get("max_drawdown"),
               cfg.max_drawdown, (metrics.get("max_drawdown") or 0) <= cfg.max_drawdown)
    if cfg.min_win_rate is not None:
        wr = metrics.get("win_rate")
        _check("min_win_rate", wr, cfg.min_win_rate,
               wr is not None and wr >= cfg.min_win_rate)
    if cfg.min_profit_factor is not None:
        pf = metrics.get("profit_factor")
        _check("min_profit_factor", pf, cfg.min_profit_factor,
               pf is not None and pf >= cfg.min_profit_factor)
    if cfg.min_avg_ret is not None:
        ar = metrics.get("avg_ret")
        _check("min_avg_ret", ar, cfg.min_avg_ret,
               ar is not None and ar >= cfg.min_avg_ret)

    all_passed = all(c.passed for c in checks)
    if n < cfg.min_trades:
        status = STATUS_DRAFT
    elif all_passed:
        status = STATUS_GATED
    else:
        status = STATUS_REJECTED
    return GateResult(status=status, passed=all_passed and status == STATUS_GATED,
                      checks=checks)


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)
