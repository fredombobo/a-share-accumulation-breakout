"""
横盘吸筹 → 启动行情 选股系统
================================
策略逻辑：
  1. 全市场初筛（排除 ST/退市/流动性差/次新）
  2. 横盘吸筹识别：1~6 个月（约20~125交易日）箱体振幅收窄、趋势平坦
  3. 启动信号确认：放量突破横盘上沿 + 均线拐头 + 资金净流入
  4. 基本面过滤：PE/PB 合理、市值适中
  5. 综合打分 → A池 Top15（strict）+ B池观察

数据通道（全部免费、无需 token）：
  - 全市场列表/快照：akshare（新浪源）
  - 个股日线K线：腾讯 ifzq 接口
  - 个股资金流：新浪 MoneyFlow 接口
  - 实时行情/基本面：腾讯 qt.gtimg.cn
"""

from __future__ import annotations

import os

# ── 环境修复：清除可能被 Hermes 注入的 PYTHONPATH 与代理 ──
os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

# ── 参数配置 ──
# 横盘时长：用户要求 1~6 个月均可（按 A 股交易日近似 20~125 日）
BOX_MIN_DAYS = 20           # 最短横盘 ≈ 1 个月
BOX_MAX_DAYS = 125          # 最长横盘 ≈ 6 个月（同时作为箱体搜索观察窗）
HORIZON_DAYS = 160          # 拉取/缓存 K 线回看（须 ≥ BOX_MAX_DAYS + 余量）
BOX_MAX_AMP = 0.26          # 稳健振幅上限 (阻力-支撑)/中轴；时长可自适应略放宽
TREND_SLOPE_LIMIT = 0.0025  # 横盘期归一化日斜率上限（更严，拒绝慢牛通道）
VOL_SHRINK_RATIO = 0.80     # 箱体后段/前段均量上限（缩量吸筹，加分项）
BREAKOUT_VOL_RATIO = 1.6    # 突破日量能 / 横盘均量 下限（放量倍数）
BREAKOUT_CHG_MIN = 0.02     # 突破日最小涨幅 2%
BREAKOUT_CHG_MAX = 0.095    # 突破日最大涨幅 9.5%（避免追高涨停）
FUND_FLOW_DAYS = 5          # 资金流确认窗口
FUND_FLOW_MIN_RATIO = 0.0   # 近N日主力净流入 / 同期成交额 下限

# ── 基本面过滤 ──
MIN_PRICE = 3.0             # 最低股价
MIN_MV_YI = 30.0            # 最小总市值（亿）
MAX_MV_YI = 3000.0          # 最大总市值（亿）
MIN_PE, MAX_PE = 0.0, 60.0  # PE(TTM) 区间
MIN_PB, MAX_PB = 0.0, 12.0  # PB 区间
MIN_LIST_DAYS = 250         # 上市至少满1年（约250交易日）

# ── 输出 ──
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_BASE_DIR, "out")
CACHE_DIR = os.path.join(OUT_DIR, "cache")
CHART_DIR = os.path.join(OUT_DIR, "charts")
# A 池默认可交易数量（胜率优先，不再默认凑 50）
TOP_N = 15
TOP_N_TRADE = 15          # A 池 strict
TOP_N_WATCH = 30          # B 池观察
INCLUDE_RELAXED_IN_A = False  # relaxed 默认进 B 池
BUILD_WATCH_POOL = True      # 是否生成 B 池（theme_fill 仅进 B）

# 多核心并行（0=自动 cpu_count-1，1=单进程调试）
SCAN_WORKERS = 0

# ── 主题：软偏好（加分排序），不再硬凑每板块 5 只进 A 池 ──
THEME_MIN_PER_SECTOR = 5  # 仅 B 池观察列表可选使用
REQUIRED_THEMES = ("AI应用", "半导体", "光模块", "机器人", "电力", "芯片")
THEME_SOFT_BONUS = 2.0  # 压低主题加分，避免盖过「长横盘+明确信号」

# 放宽参数（仅用于 B 池补充，不进默认 A 池）
RELAXED_BOX_MAX_AMP = 0.36
RELAXED_BREAKOUT_VOL_RATIO = 1.25
RELAXED_BREAKOUT_CHG_MIN = 0.012
RELAXED_BREAKOUT_CHG_MAX = 0.12
RELAXED_BREAKOUT_WINDOW_DAYS = 10
RELAXED_FUND_FLOW_MIN_RATIO = -0.02

# 资金流质量：近窗净流入为正的最少天数
FUND_POSITIVE_DAYS_MIN = 2

# 交易计划
STOP_LOSS_PCT = 0.07
TARGET_PCT_1 = 0.12
MAX_HOLD_DAYS = 15

# ── 数据源 ──
TENCNET_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCNET_QUOTE_URL = "https://qt.gtimg.cn/q="
SINA_MONEYFLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_zjlrqs"
)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
