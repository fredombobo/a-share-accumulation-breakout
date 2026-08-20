"""instrument 级交易规则：默认规则 + 查询/落库。

默认保守研究成本配置（仿真假设，非实际券商费率）：
  - 股票：双边佣金 5bp、最低 5 元(500分)；卖出税费 10bp；其他费用 1bp；滑点 10bp；整手 100
  - ETF ：双边佣金 5bp、最低 5 元；卖出税费 0；其他费用 1bp；滑点 5bp；整手 100
单标的可覆盖（instrument_rules 表按 ts_code 覆盖）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import COMMISSION_MIN_YUAN, COMMISSION_RATE

from .errors import ERR_UNKNOWN_INSTRUMENT, DomainError

_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class InstrumentRule:
    ts_code: str
    inst_type: str            # STOCK | ETF
    commission_bps: int = int(COMMISSION_RATE * 10_000)   # 与 config 统一（万五）
    min_commission_fen: int = int(COMMISSION_MIN_YUAN * 100)  # 最低 5 元
    sell_tax_bps: int = 10    # 股票 10bp；ETF 0
    other_fee_bps: int = 1
    slippage_bps: int = 10    # 股票 10bp；ETF 5bp
    lot_size: int = 100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _infer_inst_type(ts_code: str) -> str:
    """按代码推断类型：51/15/16/56 开头 → ETF；其余 → STOCK。"""
    num = ts_code.split(".")[0]
    if num.startswith(("51", "15", "16", "56", "58")):
        return "ETF"
    return "STOCK"


def default_rule(ts_code: str, inst_type: str | None = None) -> InstrumentRule:
    """生成默认规则（不进迁移；未知类型抛领域错误）。"""
    t = (inst_type or _infer_inst_type(ts_code)).upper()
    if t not in ("STOCK", "ETF"):
        raise DomainError(
            ERR_UNKNOWN_INSTRUMENT,
            f"未知标的类型: {t}",
            details={"ts_code": ts_code, "inst_type": t},
        )
    if t == "ETF":
        return InstrumentRule(ts_code=ts_code, inst_type="ETF",
                              sell_tax_bps=0, slippage_bps=5)
    return InstrumentRule(ts_code=ts_code, inst_type="STOCK")


def peek_rule(db_path: str | Path, ts_code: str) -> InstrumentRule:
    """Read an instrument rule without creating database state."""
    db_path = Path(db_path)
    import sqlite3

    with sqlite3.connect(str(db_path), timeout=30) as conn:
        row = conn.execute(
            "SELECT ts_code, inst_type, commission_bps, min_commission_fen,"
            " sell_tax_bps, other_fee_bps, slippage_bps, lot_size"
            " FROM instrument_rules WHERE ts_code=?", (ts_code,),
        ).fetchone()
    return InstrumentRule(*row) if row else default_rule(ts_code)


def require_rule(db_path: str | Path, ts_code: str) -> InstrumentRule:
    """严格路径（v2 P1.2）：无规则行 → 显式失败，禁止默认推断/自动落库兜底。

    订单/回测等需要「缺 instrument rule 返回明确失败」的路径必须调用本函数；
    遗留自动创建路径（get_rule）仅保留给历史流程。
    """
    db_path = Path(db_path)
    import sqlite3

    with sqlite3.connect(str(db_path), timeout=30) as conn:
        row = conn.execute(
            "SELECT ts_code, inst_type, commission_bps, min_commission_fen,"
            " sell_tax_bps, other_fee_bps, slippage_bps, lot_size"
            " FROM instrument_rules WHERE ts_code=?", (ts_code,),
        ).fetchone()
    if row is None:
        raise DomainError(
            ERR_UNKNOWN_INSTRUMENT,
            f"缺少 instrument 规则: {ts_code}（严格路径禁止默认值兜底）",
            details={"ts_code": ts_code},
        )
    return InstrumentRule(*row)


def get_rule(db_path: str | Path, ts_code: str) -> InstrumentRule:
    """查规则表 → 无则创建默认行（首次使用自动落库）→ 未知类型抛领域错误。"""
    db_path = Path(db_path)
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT ts_code, inst_type, commission_bps, min_commission_fen,"
            " sell_tax_bps, other_fee_bps, slippage_bps, lot_size"
            " FROM instrument_rules WHERE ts_code=?", (ts_code,)
        ).fetchone()
        if row:
            return InstrumentRule(*row)
    finally:
        conn.close()

    # 无规则：生成默认并落库（单独事务，失败不影响读取）
    rule = default_rule(ts_code)
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR IGNORE INTO instrument_rules (ts_code, inst_type,"
            " commission_bps, min_commission_fen, sell_tax_bps, other_fee_bps,"
            " slippage_bps, lot_size, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (rule.ts_code, rule.inst_type, rule.commission_bps, rule.min_commission_fen,
             rule.sell_tax_bps, rule.other_fee_bps, rule.slippage_bps, rule.lot_size,
             datetime.now(_TZ).isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
    return rule
