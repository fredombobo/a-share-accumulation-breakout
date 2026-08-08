"""
主题板块映射 + 配额选股
======================
强制覆盖主题（每板块至少 N 只）：
  AI应用 / 半导体 / 光模块 / 机器人 / 电力 / 芯片

映射依据：tushare industry 标签 + 证券简称关键词。
一只股票可命中多个主题；配额填充时按「更细分优先」顺序占坑，避免重复计入。
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

import pandas as pd

# 配额填充顺序：越细分越靠前，避免被宽泛主题抢走名额
THEME_FILL_ORDER = ["光模块", "芯片", "机器人", "AI应用", "半导体", "电力"]

# 每主题最低数量（用户强制）
THEME_MIN_COUNT = {
    "AI应用": 5,
    "半导体": 5,
    "光模块": 5,
    "机器人": 5,
    "电力": 5,
    "芯片": 5,
}

# industry 精确/包含匹配
_INDUSTRY_RULES: dict[str, tuple[str, ...]] = {
    "AI应用": ("软件服务", "互联网", "IT设备", "电信运营"),
    "半导体": ("半导体",),
    "光模块": ("通信设备", "元器件"),
    "机器人": ("专用机械", "电气设备", "机床制造", "机械基件", "电器仪表"),
    "电力": ("新型电力", "水力发电", "火力发电", "电气设备", "供气供热"),
    "芯片": ("半导体",),
}

# 名称关键词（命中即归属；光模块/芯片/机器人优先靠名称收窄）
_NAME_RULES: dict[str, tuple[str, ...]] = {
    "AI应用": (
        "人工智能", "算力", "服务器", "云计算", "数据中", "信创",
        "操作系统", "网络安全", "软件", "智能", "数字经济",
    ),
    "半导体": (
        "半导体", "硅片", "晶圆", "光刻", "掩模", "靶材", "电子特气",
        "湿电子", "抛光", "刻蚀", "薄膜", "封装材料", "设备",
    ),
    "光模块": (
        "光模块", "光通信", "光器件", "光芯片", "光纤", "光缆", "光电",
        "光迅", "中际", "新易盛", "天孚", "源杰", "剑桥", "联特",
        "华工科技", "光库", "仕佳", "长飞", "烽火", "亨通", "太辰光",
        "德科立", "光峰", "中瓷", "天孚通信",
    ),
    "机器人": (
        "机器人", "自动化", "工业母机", "伺服", "减速器", "机器视觉",
        "数控", "人形", "协作机器", "智能装备", "工控", "PLC",
        "汇川", "埃斯顿", "绿的谐波", "双环传动",
    ),
    "电力": (
        "电力", "电网", "特高压", "水电", "火电", "核电",
        "储能", "输变电", "配电", "充电桩", "风电", "绿电",
        "华能", "大唐", "国电南瑞", "南网", "三峡", "许继电气", "平高电气",
        "天然气", "燃气",  # 供气供热里的公用事业
        # 注意：勿用过短子串如「平高」(会误伤「隆平高科」)
    ),
    "芯片": (
        "芯片", "集成电路", "封测", "MCU", "GPU", "CPU", "存储芯片",
        "模拟芯片", "射频芯片", "功率半导体", "IGBT", "碳化硅", "氮化镓",
        "中芯", "华虹", "韦尔", "兆易", "卓胜", "澜起", "寒武纪",
        "北方华创", "中微公司", "拓荆", "华峰测", "长电科技", "通富微",
        "晶方科技", "汇顶", "兆易创新", "圣邦", "思瑞浦", "艾为",
    ),
}

# 名称「强关键词」：即使 industry 不匹配也计入
_NAME_STRONG: dict[str, tuple[str, ...]] = {
    "光模块": (
        "光模块", "光通信", "光器件", "光芯片", "光迅", "中际旭创", "新易盛",
        "天孚通信", "源杰科技", "剑桥科技", "联特科技", "华工科技", "光库科技",
        "仕佳光子", "长飞光纤", "光峰科技", "德科立", "太辰光", "烽火通信",
        "亨通光电", "中瓷电子",
    ),
    "机器人": (
        "机器人", "工业母机", "减速器", "伺服系统", "协作机器", "人形机器",
        "埃斯顿", "绿的谐波", "汇川技术",
    ),
    "芯片": (
        "芯片", "集成电路", "封测", "中芯国际", "华虹", "寒武纪", "兆易创新",
        "韦尔股份", "卓胜微", "澜起科技", "长电科技", "通富微电", "北方华创",
        "中微公司",
    ),
    "AI应用": (
        "人工智能", "算力", "寒武纪", "科大讯飞", "云从科技", "海康威视",
        "大华股份", "浪潮信息", "中科曙光", "金山办公",
    ),
    "电力": (
        "电力", "特高压", "储能", "南网", "华能", "大唐", "三峡",
        "国电南瑞", "国电电力", "许继电气", "平高电气", "思源电气",
    ),
    "半导体": ("半导体", "硅片", "晶圆", "光刻胶", "北方华创", "中微公司"),
}


def match_themes(industry: str | None, name: str | None) -> list[str]:
    """返回该股命中的主题列表（按 THEME_FILL_ORDER 排序）。

    同一 (industry, name) 结果由 lru_cache 复用，避免 5000 行逐行重复计算。
    """
    return list(_match_themes_cached(str(industry or "").strip(), str(name or "").strip()))


@lru_cache(maxsize=16384)
def _match_themes_cached(ind: str, nm: str) -> tuple[str, ...]:
    nm_u = nm.upper()
    hits: list[str] = []

    def _name_hit(kws: tuple[str, ...]) -> bool:
        for kw in kws:
            if not kw:
                continue
            if kw.isascii():
                if kw.upper() in nm_u:
                    return True
            elif kw in nm:
                return True
        return False

    for theme in THEME_FILL_ORDER:
        ok = False
        # 1) 强名称
        if _name_hit(_NAME_STRONG.get(theme, ())):
            ok = True
        # 2) industry 规则
        if not ok:
            for kw in _INDUSTRY_RULES.get(theme, ()):
                if not kw or kw not in ind:
                    continue
                if theme == "光模块":
                    # 通信设备/元器件：名称需带「光」或光模块关键词
                    if _name_hit(_NAME_RULES["光模块"]) or ("光" in nm):
                        ok = True
                        break
                    continue
                if theme == "机器人":
                    if ind in ("机床制造",) or _name_hit(_NAME_RULES["机器人"]):
                        ok = True
                        break
                    continue
                if theme == "电力" and kw == "电气设备":
                    if _name_hit(_NAME_RULES["电力"]):
                        ok = True
                        break
                    continue
                if theme == "芯片" and kw == "半导体":
                    # 芯片：半导体行业 + 芯片侧关键词；不够时由强制补齐层从半导体池拆分
                    if _name_hit(_NAME_RULES["芯片"]):
                        ok = True
                        break
                    continue
                if theme == "半导体" and kw == "半导体":
                    ok = True
                    break
                ok = True
                break
        # 3) 普通名称规则
        if not ok and _name_hit(_NAME_RULES.get(theme, ())):
            ok = True
        # 4) 芯片：半导体行业内「前半」可作芯片候选——名称含设计/制造/封测味，
        #    或行业=半导体且不在纯材料设备弱匹配；默认半导体行业都可进芯片池，
        #    由配额独占占坑保证与「半导体」主题拆成不同股票。
        if not ok and theme == "芯片" and ind == "半导体":
            ok = True
        if ok:
            hits.append(theme)
    return tuple(hits)


def primary_theme(industry: str | None, name: str | None) -> str:
    themes = match_themes(industry, name)
    return themes[0] if themes else "其他"


def _dedup_themes_map(industry: pd.Series, name: pd.Series) -> dict[tuple[str, str], list[str]]:
    """对 (industry, name) 去重后批量计算主题，返回 {(ind, name): [themes]}。

    避免对数千行逐行重复跑 match_themes；唯一组合数通常远小于行数。
    """
    ind = industry.map(lambda v: str(v or "").strip())
    nm = name.map(lambda v: str(v or "").strip())
    uniq = pd.DataFrame({"ind": ind, "nm": nm}).drop_duplicates()
    return {
        (r["ind"], r["nm"]): match_themes(r["ind"], r["nm"])
        for _, r in uniq.iterrows()
    }


def annotate_themes(
    df: pd.DataFrame,
    industry_col: str = "行业",
    name_col: str = "名称",
) -> pd.DataFrame:
    """为结果表增加 主题板块 / 主题列表 列。兼容英文列名。"""
    out = df.copy()
    if industry_col not in out.columns and "industry" in out.columns:
        industry_col = "industry"
    if name_col not in out.columns and "name" in out.columns:
        name_col = "name"

    lookup = _dedup_themes_map(out[industry_col], out[name_col])
    ind = out[industry_col].map(lambda v: str(v or "").strip())
    nm = out[name_col].map(lambda v: str(v or "").strip())
    themes_list = [lookup[(i, n)] for i, n in zip(ind, nm)]
    out["主题列表"] = [",".join(t) if t else "其他" for t in themes_list]
    out["主题板块"] = [t[0] if t else "其他" for t in themes_list]
    return out


def select_with_sector_quota(
    df: pd.DataFrame,
    top_n: int = 50,
    score_col: str = "综合分",
    min_counts: dict[str, int] | None = None,
    code_col: str = "ts_code",
) -> tuple[pd.DataFrame, dict]:
    """按主题配额选取 Top N。

    算法：
      1) 按 THEME_FILL_ORDER 为每个主题取最高分、未入选的 min_count 只
      2) 剩余名额按总分从高到低补齐至 top_n
      3) 最终按总分降序

    返回 (selected_df, report)
    report 含每主题实际数量、缺口、是否满足强制配额。
    """
    if df is None or df.empty:
        empty_report = {
            "top_n": top_n,
            "selected": 0,
            "theme_counts": {k: 0 for k in THEME_FILL_ORDER},
            "theme_shortfall": dict(min_counts or THEME_MIN_COUNT),
            "quota_ok": False,
            "notes": ["候选为空"],
        }
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame(), empty_report

    min_counts = dict(min_counts or THEME_MIN_COUNT)
    if score_col not in df.columns:
        if "total_score" in df.columns:
            score_col = "total_score"
        else:
            raise KeyError(f"结果表缺少排序分列：{score_col}/total_score")
    if code_col not in df.columns:
        if "代码" in df.columns:
            code_col = "代码"
        else:
            raise KeyError("结果表缺少 ts_code/代码 列")

    work = annotate_themes(df)
    work = work.sort_values(score_col, ascending=False).reset_index(drop=True)

    selected_idx: list[int] = []
    selected_codes: set[str] = set()
    theme_counts = {k: 0 for k in THEME_FILL_ORDER}
    notes: list[str] = []

    def _themes_of(row) -> list[str]:
        raw = str(row.get("主题列表") or "")
        if not raw or raw == "其他":
            return []
        return [x for x in raw.split(",") if x]

    # 1) 强制配额：按主题独占占坑（一只股票只计入一个主题）
    assigned_theme: dict[str, str] = {}  # code -> theme
    for theme in THEME_FILL_ORDER:
        need = int(min_counts.get(theme, 0))
        if need <= 0:
            continue
        taken = 0
        for i, row in work.iterrows():
            if taken >= need:
                break
            code = str(row[code_col])
            if code in selected_codes:
                continue
            if theme not in _themes_of(row):
                continue
            # 半导体：独占占坑已保证与已选芯片不重复，无需再按名称过滤
            selected_idx.append(int(i))
            selected_codes.add(code)
            assigned_theme[code] = theme
            theme_counts[theme] += 1
            taken += 1
        if taken < need:
            notes.append(f"{theme} 仅凑到 {taken}/{need}")

    # 2) 按分补齐到 top_n（不强制改主题）
    for i, row in work.iterrows():
        if len(selected_idx) >= top_n:
            break
        code = str(row[code_col])
        if code in selected_codes:
            continue
        selected_idx.append(int(i))
        selected_codes.add(code)
        ths = _themes_of(row)
        if ths and code not in assigned_theme:
            assigned_theme[code] = ths[0]

    selected = work.loc[selected_idx].copy()
    # 写回独占主主题，供展示与统计
    if "主题板块" not in selected.columns:
        selected["主题板块"] = "其他"
    for i, row in selected.iterrows():
        code = str(row[code_col])
        if code in assigned_theme:
            selected.at[i, "主题板块"] = assigned_theme[code]

    selected = selected.sort_values(score_col, ascending=False).reset_index(drop=True)
    if len(selected) > top_n:
        selected = selected.head(top_n).copy()

    # 按主主题独占计数（不再用多标签虚增）
    recount = {k: 0 for k in THEME_FILL_ORDER}
    for _, row in selected.iterrows():
        th = str(row.get("主题板块") or "")
        if th in recount:
            recount[th] += 1
    shortfall = {
        th: max(0, min_counts.get(th, 0) - recount.get(th, 0))
        for th in THEME_FILL_ORDER
    }
    report = {
        "top_n": top_n,
        "selected": len(selected),
        "theme_counts": recount,
        "theme_shortfall": shortfall,
        "quota_ok": all(v == 0 for v in shortfall.values()) and len(selected) >= min(
            top_n, sum(min_counts.values())
        ),
        "notes": notes,
    }
    return selected, report


def theme_universe_mask(basic: pd.DataFrame, themes: Iterable[str]) -> pd.Series:
    """stock_basic 上标记是否属于给定主题集合。"""
    themes = set(themes)
    mask = []
    for _, r in basic.iterrows():
        hits = set(match_themes(r.get("industry"), r.get("name")))
        mask.append(bool(hits & themes))
    return pd.Series(mask, index=basic.index)


def format_quota_report(report: dict) -> str:
    lines = [
        f"入选 {report.get('selected')}/{report.get('top_n')}  配额满足={report.get('quota_ok')}"
    ]
    counts = report.get("theme_counts") or {}
    short = report.get("theme_shortfall") or {}
    for th in THEME_FILL_ORDER:
        lines.append(f"  - {th}: {counts.get(th, 0)} 只 (缺口 {short.get(th, 0)})")
    for n in report.get("notes") or []:
        lines.append(f"  ! {n}")
    return "\n".join(lines)
