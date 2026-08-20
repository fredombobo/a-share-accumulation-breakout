"""P2.2 测试：研究/纸面一致性——同一 fixture 产生相同成交 hash。"""
from __future__ import annotations

import hashlib
import json

from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import Quote


def _quote(**over) -> Quote:
    base = {
        "ts_code": "000001.SZ", "trade_date": "20260810",
        "open_micro": 10_000_000, "high_micro": 10_500_000, "low_micro": 9_800_000,
        "close_micro": 10_200_000, "vol": 1_000_000, "amount_fen": 10_000_000,
        "pre_close_micro": 9_900_000, "available_at": "2026-08-10T16:00:00+08:00",
    }
    base.update(over)
    return Quote(**base)


def _fill_hash(fill) -> str:
    """成交的规范指纹：可成交量/价/费用/现金变动/持仓数量。"""
    blob = json.dumps(
        {
            "filled": fill.filled,
            "qty": fill.qty,
            "price_micro": fill.price_micro,
            "notional_fen": fill.notional_fen,
            "fees": fill.fees.to_dict(),
            "cash_delta_fen": fill.cash_delta_fen,
            "max_qty": fill.max_qty,
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _research_fill(q: Quote):
    """研究路径：参与率 5%、整手 100、现金充足。"""
    return compute_fill(
        q, FillRequest(ts_code=q.ts_code, side="BUY", trade_date=q.trade_date,
                       input_hash="research", participation_bps=500, lot_size=100,
                       cash_available_fen=2_000_000_00)
    )


def _paper_fill(q: Quote):
    """纸面路径：同一参数（参与率 5%、整手 100、现金充足）。"""
    return compute_fill(
        q, FillRequest(ts_code=q.ts_code, side="BUY", trade_date=q.trade_date,
                       input_hash="paper", participation_bps=500, lot_size=100,
                       cash_available_fen=2_000_000_00)
    )


def test_research_paper_fill_hash_parity():
    """同一 fixture：研究/纸面成交 hash 完全一致。"""
    q = _quote(open_micro=10_000_000, high_micro=10_500_000, low_micro=9_800_000,
               vol=1_000_000)
    research = _research_fill(q)
    paper = _paper_fill(q)
    assert research.filled is True and paper.filled is True
    assert _fill_hash(research) == _fill_hash(paper)
    assert research.qty == paper.qty == 50_000  # 5% 参与率
    assert research.price_micro == paper.price_micro


def test_parity_with_different_input_hash_but_same_semantics():
    """input_hash 不同（防重复键），但成交语义 hash 一致。"""
    q = _quote(vol=100_000)
    r = _research_fill(q)
    p = _paper_fill(q)
    assert _fill_hash(r) == _fill_hash(p)


def test_parity_zero_fill_on_one_side_block():
    """一字涨停买：研究与纸面同样零成交。"""
    up = 10_890_000
    q = _quote(open_micro=up, high_micro=up, low_micro=up, pre_close_micro=9_900_000)
    r = _research_fill(q)
    p = _paper_fill(q)
    assert r.filled is False and p.filled is False
    assert _fill_hash(r) == _fill_hash(p)
