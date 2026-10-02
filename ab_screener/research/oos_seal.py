"""样本外封存（ADR-022 决策 2）：让「OOS 在开发期未被查看」由代码强制，而不是自述。

- 封存配置：`configs/research/oos_seal.json`（入库，冻结）。
- 解封台账：`runtime/research/oos_unseal_ledger.jsonl`（追加式运行证据，不入库）。
- 研究读取任何与封存区间有交集的窗口前必须调用 `assert_window_allowed`；
  未解封即拒绝。每个假设只允许解封一次，且必须提供预登记文件的 SHA-256，
  第二次解封拒绝（再看一次就不是未见样本）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEAL_PATH = _ROOT / "configs" / "research" / "oos_seal.json"
DEFAULT_LEDGER_PATH = _ROOT / "runtime" / "research" / "oos_unseal_ledger.jsonl"


class OosSealError(RuntimeError):
    """封存区间被未授权访问，或解封请求不合法。"""


def _norm(day: str) -> str:
    text = str(day).replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise OosSealError(f"日期必须是 YYYYMMDD: {day!r}")
    return text


def load_seals(seal_path: Path = DEFAULT_SEAL_PATH) -> list[dict[str, Any]]:
    doc = json.loads(Path(seal_path).read_text(encoding="utf-8"))
    return list(doc.get("seals") or [])


def seal_for(hypothesis_id: str, seal_path: Path = DEFAULT_SEAL_PATH) -> dict[str, Any] | None:
    for seal in load_seals(seal_path):
        if hypothesis_id in seal.get("hypotheses", []):
            return seal
    return None


def ledger_entries(hypothesis_id: str, ledger_path: Path = DEFAULT_LEDGER_PATH) -> list[dict[str, Any]]:
    path = Path(ledger_path)
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("hypothesis_id") == hypothesis_id:
                entries.append(record)
    return entries


def assert_window_allowed(
    hypothesis_id: str,
    start: str,
    end: str,
    *,
    seal_path: Path = DEFAULT_SEAL_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> None:
    """窗口与封存区间有交集且尚未解封 → 拒绝。"""
    lo, hi = _norm(start), _norm(end)
    if lo > hi:
        raise OosSealError(f"窗口起点晚于终点: {start} > {end}")
    seal = seal_for(hypothesis_id, seal_path)
    if seal is None:
        return
    sealed_lo, sealed_hi = _norm(seal["sealed_from"]), _norm(seal["sealed_to"])
    if hi < sealed_lo or lo > sealed_hi:
        return
    if not ledger_entries(hypothesis_id, ledger_path):
        raise OosSealError(
            f"{hypothesis_id} 的样本外 {sealed_lo}~{sealed_hi} 仍封存；"
            f"请求窗口 {lo}~{hi} 与之重叠。IS 通过 G2 后才可一次性解封。"
        )


def unseal(
    hypothesis_id: str,
    *,
    registration_sha256: str,
    reason: str,
    seal_path: Path = DEFAULT_SEAL_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    """一次性解封：写入台账并返回记录；重复解封拒绝。"""
    if seal_for(hypothesis_id, seal_path) is None:
        raise OosSealError(f"{hypothesis_id} 不在任何封存中")
    if len(registration_sha256) != 64:
        raise OosSealError("必须提供预登记 registration.json 的 SHA-256")
    if not reason.strip():
        raise OosSealError("必须写明解封理由（例如 G2 通过的证据哈希）")
    if ledger_entries(hypothesis_id, ledger_path):
        raise OosSealError(f"{hypothesis_id} 已解封过一次；OOS 只能看一次")
    record = {
        "hypothesis_id": hypothesis_id,
        "unsealed_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "registration_sha256": registration_sha256,
        "reason": reason.strip(),
    }
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
