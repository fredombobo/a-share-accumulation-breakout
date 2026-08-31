"""龙虎榜研究信号契约（T08）。永远 research_only。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.lhb_contracts import SIGNAL_STATUS_VALUES, parse_enum, parse_trade_date

POLICY_VERSION_DEFAULT = "lhb-signal-v1"
HARD_VETOES = (
    "DATA_INCOMPLETE",
    "IDENTITY_LOW_CONF",
    "UNFILLABLE",
    "ILLIQUID",
    "CROWDED_HIGH",
    "SEVERE_ABNORMAL",
)


def policy_version_hash(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()[:16]


def earliest_executable_at(disclose_date: str, calendar: list[str]) -> str:
    parse_trade_date(disclose_date)
    nxt = next((d for d in calendar if d > disclose_date), None)
    if nxt is None:
        raise ValueError("无下一交易日，不能生成可执行时间")
    return f"{nxt[:4]}-{nxt[4:6]}-{nxt[6:8]}T09:30:00+08:00"


@dataclass(frozen=True)
class SignalInput:
    ts_code: str
    disclose_date: str
    disclose_at: str
    net_yuan: float
    amount_yuan: float
    adv20_yuan: float
    purity: float
    independent_actors: int
    identity_confidence: float
    identity_grade: str
    turnover: float
    data_complete: bool
    next_bar_unfillable: bool
    next_bar_suspended: bool
    liquid: bool
    crowded: bool
    severe_abnormal: bool
    calendar: tuple[str, ...]
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    data_version: str = "d1"
    identity_version: str = "i1"


def score_components(inp: SignalInput, policy: dict[str, Any]) -> dict[str, float]:
    amount = max(inp.amount_yuan, 1.0)
    adv = max(inp.adv20_yuan, 1.0)
    return {
        "net_over_amount": inp.net_yuan / amount,
        "net_over_adv20": inp.net_yuan / adv,
        "purity": inp.purity,
        "independent_actors": float(inp.independent_actors),
        "identity_confidence": inp.identity_confidence,
        "turnover": inp.turnover,
    }


def collect_vetoes(inp: SignalInput, policy: dict[str, Any]) -> list[str]:
    vetoes: list[str] = []
    if not inp.data_complete:
        vetoes.append("DATA_INCOMPLETE")
    if inp.identity_confidence < float(policy.get("min_identity_confidence", 0.4)) or inp.identity_grade == "C":
        vetoes.append("IDENTITY_LOW_CONF")
    if inp.next_bar_unfillable or inp.next_bar_suspended:
        vetoes.append("UNFILLABLE")
    if not inp.liquid:
        vetoes.append("ILLIQUID")
    if inp.crowded or inp.turnover > float(policy.get("max_turnover_crowd", 25)):
        vetoes.append("CROWDED_HIGH")
    if inp.severe_abnormal:
        vetoes.append("SEVERE_ABNORMAL")
    return vetoes


def decide_status(scores: dict[str, float], vetoes: list[str], policy: dict[str, Any]) -> str:
    if "UNFILLABLE" in vetoes or "ILLIQUID" in vetoes or "CROWDED_HIGH" in vetoes:
        return "NO_CHASE"
    if "SEVERE_ABNORMAL" in vetoes:
        return "INVALIDATED"
    if "DATA_INCOMPLETE" in vetoes:
        return "WATCH"
    confirmed = (
        scores["net_over_amount"] >= float(policy.get("min_net_over_amount", 0.03))
        and scores["purity"] >= float(policy.get("min_purity", 0.55))
        and scores["independent_actors"] >= float(policy.get("min_independent_actors", 2))
        and "IDENTITY_LOW_CONF" not in vetoes
    )
    if confirmed and scores["net_over_adv20"] >= float(policy.get("min_net_over_adv20", 0.05)):
        return "RESEARCH_ENTRY"
    if confirmed:
        return "CONFIRMED_FLOW"
    return "WATCH"


def evaluate_signal(inp: SignalInput) -> dict[str, Any]:
    policy = dict(inp.policy) if inp.policy else {"version": POLICY_VERSION_DEFAULT}
    scores = score_components(inp, policy)
    vetoes = collect_vetoes(inp, policy)
    status = decide_status(scores, vetoes, policy)
    parse_enum(status, SIGNAL_STATUS_VALUES, label="signal_status")
    exec_at = earliest_executable_at(inp.disclose_date, list(inp.calendar))
    if exec_at <= inp.disclose_at:
        raise ValueError("最早执行时间不得早于披露时间")
    snapshot = {
        "ts_code": inp.ts_code,
        "disclose_date": inp.disclose_date,
        "disclose_at": inp.disclose_at,
        "net_yuan": inp.net_yuan,
        "amount_yuan": inp.amount_yuan,
        "adv20_yuan": inp.adv20_yuan,
        "purity": inp.purity,
        "independent_actors": inp.independent_actors,
        "identity_confidence": inp.identity_confidence,
        "identity_grade": inp.identity_grade,
        "turnover": inp.turnover,
        "data_complete": inp.data_complete,
        "next_bar_unfillable": inp.next_bar_unfillable,
        "next_bar_suspended": inp.next_bar_suspended,
        "liquid": inp.liquid,
        "crowded": inp.crowded,
        "severe_abnormal": inp.severe_abnormal,
        "calendar": list(inp.calendar),
        "policy": policy,
        "data_version": inp.data_version,
        "identity_version": inp.identity_version,
        "feature_snapshot": inp.feature_snapshot,
        "scores": scores,
        "vetoes": vetoes,
    }
    return {
        "ts_code": inp.ts_code,
        "disclose_date": inp.disclose_date,
        "status": status,
        "research_only": True,
        "scores": scores,
        "vetoes": vetoes,
        "policy_version": str(policy.get("version", POLICY_VERSION_DEFAULT)),
        "policy_hash": policy_version_hash(policy),
        "data_version": inp.data_version,
        "identity_version": inp.identity_version,
        "disclose_at": inp.disclose_at,
        "earliest_executable_at": exec_at,
        "feature_snapshot": snapshot,
    }


def recompute_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """用快照里的原始输入重算分数与否决，不读当前 yaml。"""
    policy = dict(snapshot["policy"])
    inp = SignalInput(
        ts_code=str(snapshot.get("ts_code") or "UNKNOWN"),
        disclose_date=str(snapshot.get("disclose_date") or "19700101"),
        disclose_at=str(snapshot.get("disclose_at") or "1970-01-01T00:00:00+08:00"),
        net_yuan=float(snapshot["net_yuan"]),
        amount_yuan=float(snapshot["amount_yuan"]),
        adv20_yuan=float(snapshot["adv20_yuan"]),
        purity=float(snapshot["purity"]),
        independent_actors=int(snapshot["independent_actors"]),
        identity_confidence=float(snapshot["identity_confidence"]),
        identity_grade=str(snapshot["identity_grade"]),
        turnover=float(snapshot["turnover"]),
        data_complete=bool(snapshot["data_complete"]),
        next_bar_unfillable=bool(snapshot["next_bar_unfillable"]),
        next_bar_suspended=bool(snapshot["next_bar_suspended"]),
        liquid=bool(snapshot["liquid"]),
        crowded=bool(snapshot["crowded"]),
        severe_abnormal=bool(snapshot["severe_abnormal"]),
        calendar=tuple(snapshot.get("calendar") or ("19700102",)),
        policy=policy,
        data_version=str(snapshot.get("data_version") or "d1"),
        identity_version=str(snapshot.get("identity_version") or "i1"),
        feature_snapshot=dict(snapshot.get("feature_snapshot") or {}),
    )
    scores = score_components(inp, policy)
    vetoes = collect_vetoes(inp, policy)
    status = decide_status(scores, vetoes, policy)
    return {
        "status": status,
        "scores": scores,
        "vetoes": vetoes,
        "policy_hash": policy_version_hash(policy),
        "policy_version": str(policy.get("version", POLICY_VERSION_DEFAULT)),
    }
