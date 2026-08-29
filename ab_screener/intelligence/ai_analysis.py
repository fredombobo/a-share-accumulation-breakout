"""AI 解读服务（P8.1）：给 A 池候选生成 DeepSeek 五维评分解读。

职责边界：
- 输入：accumulation_breakout 自身 SQLite（daily/daily_basic/fina_indicator/
  moneyflow/stock_basic）与 A 池候选（scan_run_candidates，pool='A'）。
- 输出：结构化 markdown 解读，落库 `ai_insights` 缓存（(ts_code, signal_date) 幂等）。
- 非目标：不参与信号生成、不修改任何核心业务表；LLM 不可用/未配置时降级为空解读，
  绝不阻塞选股主流程（fail-open）。

架构位置：application 层，允许直接 sqlite3；API 装配层不得直接 import 本模块的
sqlite3 细节（见 scripts/check_architecture.py）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from ab_screener.ai.client import chat, has_provider
from ab_screener.ai.prompts import AB_SIGNAL_ANALYSIS_PROMPT, SYSTEM_PROMPT

_TZ = ZoneInfo("Asia/Shanghai")

# ai_insights 缓存表（非核心业务表，惰性建表；仅 AI 解读缓存，可安全重建）
_CREATE_INSIGHTS = """
CREATE TABLE IF NOT EXISTS ai_insights (
  ts_code TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  run_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  ai_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (ts_code, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_ai_insights_created ON ai_insights(created_at);
"""


class AIInsightError(RuntimeError):
    """AI 解读服务错误。"""


def _connect(db_path: str | Path, readonly: bool = True) -> sqlite3.Connection:
    p = Path(db_path)
    if not p.is_file():
        raise AIInsightError(f"数据库不存在: {p}")
    uri = f"file:{p}?mode=ro" if readonly else str(p)
    conn = sqlite3.connect(uri, uri=readonly, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ── A 池候选 ─────────────────────────────────────────────────────────────

def get_latest_run_id(db_path: str | Path) -> str | None:
    """最新一次 SUCCEEDED 扫描 run_id（按 as_of 降序，同 as_of 取最新入库）。"""
    with _connect(db_path) as conn:
        if not _table_exists(conn, "scan_runs"):
            return None
        row = conn.execute(
            "SELECT run_id FROM scan_runs WHERE status='SUCCEEDED'"
            " ORDER BY as_of DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return row["run_id"] if row else None


def get_a_pool_candidates(
    db_path: str | Path,
    *,
    top_n: int = 15,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """读取 A 池候选（strict 口径），按 total_score 降序取 top_n。

    - 指定 run_id：精确取该 run 的 A 池。
    - 未指定：取最新交易日（as_of 最大）所有 SUCCEEDED run 的 A 池并集，
      按 ts_code 去重（保留最高分），覆盖当日完整候选。
    """
    with _connect(db_path) as conn:
        if not _table_exists(conn, "scan_run_candidates"):
            return []
        if run_id:
            rows = conn.execute(
                "SELECT run_id, ts_code, pool, tier, total_score, payload_json"
                " FROM scan_run_candidates WHERE run_id=? AND pool='A'"
                " ORDER BY total_score DESC, ts_code ASC LIMIT ?",
                (run_id, int(top_n)),
            ).fetchall()
        else:
            latest = conn.execute(
                "SELECT MAX(as_of) FROM scan_runs WHERE status='SUCCEEDED'"
            ).fetchone()[0]
            if not latest:
                return []
            run_ids = [
                r[0] for r in conn.execute(
                    "SELECT run_id FROM scan_runs WHERE status='SUCCEEDED' AND as_of=?",
                    (latest,),
                ).fetchall()
            ]
            placeholders = ",".join("?" * len(run_ids))
            rows = conn.execute(
                f"SELECT run_id, ts_code, pool, tier, total_score, payload_json"
                f" FROM scan_run_candidates WHERE run_id IN ({placeholders}) AND pool='A'"
                f" ORDER BY total_score DESC, ts_code ASC",
                run_ids,
            ).fetchall()

    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except ValueError:
            payload = {}
        ts_code = r["ts_code"]
        score = float(r["total_score"] or 0)
        if ts_code not in best or score > best[ts_code]["total_score"]:
            signal_date = payload.get("breakout_date") or payload.get("trade_date") or ""
            best[ts_code] = {
                "run_id": r["run_id"],
                "ts_code": ts_code,
                "tier": r["tier"],
                "total_score": score,
                "signal_date": str(signal_date).replace("-", "")[:8],
                "signal": payload,
            }
    out = sorted(best.values(), key=lambda x: x["total_score"], reverse=True)
    return out[: int(top_n)]


# ── 数据装配 ─────────────────────────────────────────────────────────────

def _kline(db_path: str | Path, ts_code: str, n: int = 250) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, open, high, low, close, vol, amount, pct_chg"
            " FROM daily WHERE ts_code=? ORDER BY trade_date ASC",
            (ts_code,),
        ).fetchall()
    if not rows:
        return []
    return [
        {
            "trade_date": r["trade_date"],
            "open": float(r["open"] or 0),
            "high": float(r["high"] or 0),
            "low": float(r["low"] or 0),
            "close": float(r["close"] or 0),
            "vol": float(r["vol"] or 0),
            "amount": float(r["amount"] or 0),
            "pct_chg": float(r["pct_chg"] or 0),
        }
        for r in rows[-n:]
    ]


def _latest_valuation(db_path: str | Path, ts_code: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "daily_basic"):
            return None
        row = conn.execute(
            "SELECT pe, pb, total_mv, circ_mv, turnover_rate"
            " FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
    return dict(row) if row else None


def _basic_info(db_path: str | Path, ts_code: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "stock_basic"):
            return None
        row = conn.execute(
            "SELECT name, industry, market, list_date FROM stock_basic WHERE ts_code=?",
            (ts_code,),
        ).fetchone()
    return dict(row) if row else None


def _latest_financial(db_path: str | Path, ts_code: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "fina_indicator"):
            return None
        row = conn.execute(
            "SELECT roe, roe_waa, roa, grossprofit_margin, netprofit_margin,"
            " or_yoy, netprofit_yoy, debt_to_assets, current_ratio"
            " FROM fina_indicator WHERE ts_code=? ORDER BY end_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
    return dict(row) if row else None


def _latest_fund_flow(db_path: str | Path, ts_code: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "moneyflow"):
            return None
        row = conn.execute(
            "SELECT net_mf_amount, buy_elg_amount, buy_lg_amount, buy_md_amount"
            " FROM moneyflow WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
    return dict(row) if row else None


# ── 技术指标摘要（移植自 astock services/stock.py）───────────────────────

def _ema(data: list[float], period: int) -> list[float]:
    result: list[float] = []
    k = 2 / (period + 1)
    ema = data[0]
    for v in data:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _kdj(highs: list[float], lows: list[float], closes: list[float], period: int = 9) -> tuple[float, float, float]:
    if len(closes) < period:
        return 50.0, 50.0, 50.0
    low_low = min(lows[-period:])
    high_high = max(highs[-period:])
    rsv = (closes[-1] - low_low) / (high_high - low_low) * 100 if high_high != low_low else 50.0
    k = 2 / 3 * 50 + 1 / 3 * rsv
    d = 2 / 3 * 50 + 1 / 3 * k
    j = 3 * k - 2 * d
    return k, d, j


def technical_summary(kline: list[dict[str, Any]]) -> str:
    """计算 K 线技术指标摘要（MA/MACD/RSI/KDJ/量比）。"""
    if len(kline) < 20:
        return "数据不足"
    closes = [k["close"] for k in kline]
    highs = [k["high"] for k in kline]
    lows = [k["low"] for k in kline]
    volumes = [k["vol"] for k in kline]
    latest = kline[-1]

    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12[-1] - ema26[-1]
    dea = float(np.mean([ema12[i] - ema26[i] for i in range(max(0, len(closes) - 9), len(closes))]))
    macd = 2 * (dif - dea)

    rsi = _rsi(closes, 14)
    k, d, j = _kdj(highs, lows, closes, 9)

    vol_ma5 = float(np.mean(volumes[-6:-1])) if len(volumes) >= 6 else (volumes[-1] or 1)
    vol_ratio = (volumes[-1] / vol_ma5) if vol_ma5 > 0 else 1.0

    return (
        f"收盘价: {latest['close']:.2f}\n"
        f"MA5: {ma5:.2f} / MA10: {ma10:.2f} / MA20: {ma20:.2f}\n"
        f"MACD: DIF={dif:.3f} DEA={dea:.3f} 柱={macd:.3f}\n"
        f"RSI(14): {rsi:.1f}\n"
        f"KDJ: K={k:.1f} D={d:.1f} J={j:.1f}\n"
        f"量比(当日/前5日均量): {vol_ratio:.2f}\n"
    )


def _fmt_wan(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return f"{f / 1e4:.2f}亿"


def _signal_context(payload: dict[str, Any]) -> str:
    """把 A 池候选的形态事实转成 LLM 可读的确定性上下文。"""
    def g(key: str) -> Any:
        return payload.get(key)

    def _f(v: Any, nd: int = 2) -> str:
        try:
            return f"{float(v):.{nd}f}"
        except (TypeError, ValueError):
            return "—"

    box_days = g("box_days")
    box_amp = g("box_amp")
    box_high = g("box_high")
    box_low = g("box_low")
    breakout_date = g("breakout_date")
    vol_ratio = g("vol_ratio")
    pct_chg = g("breakout_pct_chg") or g("change_pct")
    ma5 = g("ma5")
    ma20 = g("ma20")
    fund_net = g("fund_net_wan")
    fund_ratio = g("fund_ratio")
    reasons = g("reasons") or ""

    pct_text = f"{_f(pct_chg)}%" if pct_chg is not None else "见系统自述"
    return (
        f"- 横盘吸筹箱体: 持续 {box_days} 个交易日，振幅 {_f(box_amp)}%（支撑 {_f(box_low)} / 压力 {_f(box_high)}）\n"
        f"- 突破: {breakout_date} 放量突破箱体上沿，量比 {_f(vol_ratio)}，当日涨幅 {pct_text}\n"
        f"- 均线: MA5={_f(ma5)} / MA20={_f(ma20)}\n"
        f"- 资金: 净流入 {_f(fund_net)} 万（量比 {_f(fund_ratio)}）\n"
        f"- 系统自述: {reasons}\n"
    )


def build_stock_context(
    db_path: str | Path,
    ts_code: str,
    signal: dict[str, Any] | None = None,
) -> dict[str, str]:
    """从本地 SQLite 装配个股上下文（astock 五维 prompt 的输入）。"""
    payload = signal or {}
    basic = _basic_info(db_path, ts_code) or {}
    val = _latest_valuation(db_path, ts_code) or {}
    fin = _latest_financial(db_path, ts_code) or {}
    flow = _latest_fund_flow(db_path, ts_code) or {}
    kline = _kline(db_path, ts_code, 250)

    name = payload.get("name") or basic.get("name") or ts_code
    industry = payload.get("industry") or basic.get("industry") or "未知"
    price = payload.get("price") or (kline[-1]["close"] if kline else None)

    lines = [
        f"名称: {name}",
        f"代码: {ts_code}",
        f"行业: {industry}",
        f"最新价: {price:.2f}" if price is not None else "最新价: 未知",
    ]
    if val:
        pe = val.get("pe")
        pb = val.get("pb")
        mv = val.get("total_mv")
        turn = val.get("turnover_rate")
        if pe is not None:
            lines.append(f"PE(TTM): {pe}")
        if pb is not None:
            lines.append(f"PB: {pb}")
        if mv:
            lines.append(f"总市值: {mv / 1e4:.1f}亿")
        if turn:
            lines.append(f"换手率: {turn:.2f}%")
    stock_info = "\n".join(lines)

    tech_text = technical_summary(kline) if kline else "暂无"

    fin_text = "暂无"
    if fin:
        parts = []
        if fin.get("roe") is not None:
            parts.append(f"ROE: {fin['roe']:.2f}%")
        if fin.get("netprofit_margin") is not None:
            parts.append(f"净利率: {fin['netprofit_margin']:.2f}%")
        if fin.get("grossprofit_margin") is not None:
            parts.append(f"毛利率: {fin['grossprofit_margin']:.2f}%")
        if fin.get("or_yoy") is not None:
            parts.append(f"营收同比: {fin['or_yoy']:+.2f}%")
        if fin.get("netprofit_yoy") is not None:
            parts.append(f"净利同比: {fin['netprofit_yoy']:+.2f}%")
        if fin.get("debt_to_assets") is not None:
            parts.append(f"资产负债率: {fin['debt_to_assets']:.2f}%")
        fin_text = "\n".join(parts)

    flow_text = "暂无"
    if flow:
        flow_text = (
            f"主力净流入: {_fmt_wan(flow.get('net_mf_amount'))}\n"
            f"超大单买入: {_fmt_wan(flow.get('buy_elg_amount'))}\n"
            f"大单买入: {_fmt_wan(flow.get('buy_lg_amount'))}"
        )

    return {
        "signal_context": _signal_context(payload) if payload else "无策略信号（独立个股解读）",
        "stock_info": stock_info,
        "kline_summary": tech_text,
        "financials": fin_text,
        "fund_flow": flow_text,
        "news": "暂无（本地系统不采集新闻，可忽略消息面评分）",
        "current_price": f"{price:.2f}" if price is not None else "未知",
    }


# ── 解读与缓存 ───────────────────────────────────────────────────────────

def _ensure_insights_table(db_path: str | Path) -> None:
    with _connect(db_path, readonly=False) as conn:
        conn.executescript(_CREATE_INSIGHTS)
        conn.commit()


def _insight_cache(db_path: str | Path, ts_code: str, signal_date: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "ai_insights"):
            return None
        row = conn.execute(
            "SELECT ts_code, signal_date, run_id, provider, prompt_hash, ai_text, created_at"
            " FROM ai_insights WHERE ts_code=? AND signal_date=?",
            (ts_code, signal_date),
        ).fetchone()
    return dict(row) if row else None


def _save_insight(
    db_path: str | Path,
    *,
    ts_code: str,
    signal_date: str,
    run_id: str,
    provider: str,
    prompt: str,
    ai_text: str,
) -> None:
    _ensure_insights_table(db_path)
    ph = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    with _connect(db_path, readonly=False) as conn:
        conn.execute(
            "INSERT INTO ai_insights (ts_code, signal_date, run_id, provider, prompt_hash, ai_text, created_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(ts_code, signal_date) DO UPDATE SET"
            " run_id=excluded.run_id, provider=excluded.provider,"
            " prompt_hash=excluded.prompt_hash, ai_text=excluded.ai_text,"
            " created_at=excluded.created_at",
            (ts_code, signal_date, run_id, provider, ph, ai_text, now),
        )
        conn.commit()


def analyze_stock(
    db_path: str | Path,
    ts_code: str,
    signal: dict[str, Any] | None = None,
    *,
    refresh: bool = False,
    provider: str = "deepseek",
    run_id: str = "",
) -> dict[str, Any]:
    """单股 AI 解读（含缓存幂等）。LLM 未配置时返回空解读（fail-open）。"""
    payload = signal or {}
    signal_date = str(payload.get("breakout_date") or payload.get("trade_date") or "").replace("-", "")[:8]
    if not signal_date:
        signal_date = datetime.now(_TZ).strftime("%Y%m%d")

    if not refresh:
        cached = _insight_cache(db_path, ts_code, signal_date)
        if cached:
            cached["available"] = True
            cached["cached"] = True
            return cached

    ctx = build_stock_context(db_path, ts_code, payload)
    prompt = AB_SIGNAL_ANALYSIS_PROMPT.format(
        signal_context=ctx["signal_context"],
        stock_info=ctx["stock_info"],
        kline_summary=ctx["kline_summary"],
        financials=ctx["financials"],
        fund_flow=ctx["fund_flow"],
        news=ctx["news"],
        current_price=ctx["current_price"],
    )

    ai_text = chat(prompt, system=SYSTEM_PROMPT, temperature=0.3, max_tokens=2500, provider=provider)
    if not ai_text:
        return {
            "ts_code": ts_code,
            "signal_date": signal_date,
            "provider": provider,
            "ai_text": "",
            "available": False,
            "reason": "LLM 未配置或调用失败（DEEPSEEK_API_KEY 缺失）",
        }

    _save_insight(
        db_path, ts_code=ts_code, signal_date=signal_date, run_id=run_id,
        provider=provider, prompt=prompt, ai_text=ai_text,
    )
    return {
        "ts_code": ts_code,
        "signal_date": signal_date,
        "run_id": run_id,
        "provider": provider,
        "ai_text": ai_text,
        "available": True,
        "cached": False,
    }


def analyze_pool(
    db_path: str | Path,
    *,
    top_n: int = 15,
    refresh: bool = False,
    provider: str = "deepseek",
) -> dict[str, Any]:
    """对 A 池 top_n 候选逐个生成 AI 解读，返回汇总。"""
    if not has_provider(provider):
        return {
            "available": False,
            "reason": f"未配置 {provider} 的 API Key（请在项目根 .env 设置 DEEPSEEK_API_KEY）",
            "candidates": [],
        }
    candidates = get_a_pool_candidates(db_path, top_n=top_n)
    run_id = candidates[0]["run_id"] if candidates else (get_latest_run_id(db_path) or "")
    results: list[dict[str, Any]] = []
    for c in candidates:
        results.append(analyze_stock(
            db_path, c["ts_code"], signal=c["signal"],
            refresh=refresh, provider=provider, run_id=run_id,
        ))
    done = [r for r in results if r.get("available")]
    return {
        "available": True,
        "run_id": run_id,
        "total_candidates": len(candidates),
        "succeeded": len(done),
        "failed": len(results) - len(done),
        "items": results,
    }


def list_insights(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """读取已缓存的 AI 解读（最新在前）。"""
    with _connect(db_path) as conn:
        if not _table_exists(conn, "ai_insights"):
            return []
        rows = conn.execute(
            "SELECT ts_code, signal_date, run_id, provider, prompt_hash, ai_text, created_at"
            " FROM ai_insights ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_insight(db_path: str | Path, ts_code: str, signal_date: str) -> dict[str, Any] | None:
    return _insight_cache(db_path, ts_code, signal_date)


def local_evidence_review(db_path: str | Path, ts_code: str) -> dict[str, Any]:
    """Deterministic, read-only review used even when no LLM is configured.

    This function performs no writes and never calls an external provider.  Its
    labels describe evidence completeness/consistency, not expected return.
    """
    code = str(ts_code).strip().upper()
    candidates = get_a_pool_candidates(db_path, top_n=500)
    candidate = next((item for item in candidates if item["ts_code"] == code), None)
    signal = dict((candidate or {}).get("signal") or {})
    kline = _kline(db_path, code, 250)
    basic = _basic_info(db_path, code)
    if basic is None and not kline:
        raise AIInsightError(f"未知股票或无本地行情: {code}")
    valuation = _latest_valuation(db_path, code) or {}
    financial = _latest_financial(db_path, code) or {}
    fund_flow = _latest_fund_flow(db_path, code) or {}
    latest = kline[-1] if kline else {}
    closes = [float(row["close"]) for row in kline if row.get("close") is not None]
    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
    close = float(latest["close"]) if latest.get("close") is not None else None
    box_days = _number(signal.get("box_days"))
    box_amp = _number(signal.get("box_amp"))
    box_high = _number(signal.get("box_high"))
    breakout_ratio = _number(
        signal.get("breakout_vol_ratio")
        if signal.get("breakout_vol_ratio") is not None
        else signal.get("vol_ratio")
    )
    evidence: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    if candidate:
        evidence.append(
            {
                "code": "A_POOL_SIGNAL",
                "label": "最新扫描为 A 池候选",
                "value": f"总分 {float(candidate.get('total_score') or 0):.1f}",
                "as_of": candidate.get("signal_date") or latest.get("trade_date"),
            }
        )
    else:
        risks.append(
            {"code": "NO_CURRENT_A_SIGNAL", "label": "最新扫描中没有该股 A 池信号"}
        )
    if box_days is not None:
        evidence.append(
            {
                "code": "BOX_STRUCTURE",
                "label": "横盘结构",
                "value": f"{int(box_days)} 日 / 振幅 {box_amp * 100:.1f}%" if box_amp is not None else f"{int(box_days)} 日",
                "as_of": candidate.get("signal_date") if candidate else latest.get("trade_date"),
            }
        )
    if breakout_ratio is not None:
        evidence.append(
            {
                "code": "BREAKOUT_VOLUME",
                "label": "突破量相对箱体均量",
                "value": f"{breakout_ratio:.2f} 倍",
                "as_of": candidate.get("signal_date") if candidate else latest.get("trade_date"),
            }
        )
    if close is not None and ma20 is not None:
        relation = close / ma20 - 1.0
        target = evidence if relation >= 0 else risks
        target.append(
            {
                "code": "MA20_RELATION",
                "label": "现价相对 MA20",
                "value": f"{relation * 100:+.2f}%",
                "as_of": latest.get("trade_date"),
            }
        )
    flow_value = _number(fund_flow.get("net_mf_amount"))
    if flow_value is not None:
        target = evidence if flow_value >= 0 else risks
        target.append(
            {
                "code": "LATEST_MAIN_FLOW",
                "label": "最新主力净额",
                "value": f"{flow_value / 1e4:+.2f} 亿元",
                "as_of": latest.get("trade_date"),
            }
        )
    if close is not None and box_high is not None and close < box_high:
        risks.append(
            {
                "code": "BACK_BELOW_BOX_HIGH",
                "label": "最新收盘已回到箱体上沿下方",
                "value": f"收盘 {close:.2f} / 箱顶 {box_high:.2f}",
                "as_of": latest.get("trade_date"),
            }
        )
    if not financial:
        risks.append({"code": "FINANCIAL_DATA_MISSING", "label": "财务证据缺失"})

    if not candidate:
        verdict = "INSUFFICIENT_EVIDENCE"
        verdict_label = "证据不足"
    elif len(risks) >= len(evidence):
        verdict = "MIXED_EVIDENCE"
        verdict_label = "证据混合，先核对风险"
    else:
        verdict = "SUPPORTS_MONITORING"
        verdict_label = "证据支持继续观察"
    signal_date = str(
        (candidate or {}).get("signal_date") or latest.get("trade_date") or ""
    ).replace("-", "")[:8]
    cached = get_insight(db_path, code, signal_date) if signal_date else None
    return {
        "ts_code": code,
        "name": signal.get("name") or (basic or {}).get("name") or code,
        "industry": signal.get("industry") or (basic or {}).get("industry") or "未分类",
        "verdict": verdict,
        "verdict_label": verdict_label,
        "as_of": latest.get("trade_date"),
        "signal_date": signal_date or None,
        "evidence": evidence[:5],
        "risks": risks[:5],
        "data": {
            "close": close,
            "pe": _number(valuation.get("pe")),
            "pb": _number(valuation.get("pb")),
            "roe": _number(financial.get("roe")),
            "box_high": box_high,
            "box_days": box_days,
            "breakout_vol_ratio": breakout_ratio,
        },
        "external_ai": cached,
        "boundary": {
            "read_only": True,
            "changes_scan_or_signal": False,
            "triggers_order": False,
            "message": "AI 只解释本地证据，不改变每日选股、研究晋级或任何交易状态。",
        },
    }


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None
