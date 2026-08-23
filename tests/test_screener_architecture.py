"""V2R-A 架构职责边界：拆分模块各自职责单一 + 公共 facade 兼容。

断言：
  - data_loader   只读/标准化输入，不含候选/打分/编排函数
  - prefilter     候选集合 + 理由，不含打分/编排函数
  - evaluator     单标的结果（阶梯/打分/软分/主题观察/信号检测），不含 run_scan
  - orchestrator  进程/取消/进度/排序/聚合（run_scan 主体），不定义单标的评分函数
  - ab_screener/run_screener.py 为公共 facade（<350 行），旧 import 与子进程 spawn 兼容
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCREENER = ROOT / "ab_screener" / "screener"


def _defined_funcs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_screener_package_modules_exist():
    for name in ("__init__", "data_loader", "prefilter", "evaluator", "orchestrator"):
        assert (SCREENER / f"{name}.py").is_file(), f"缺少 {name}.py"


def test_data_loader_is_read_only_normalization():
    funcs = _defined_funcs(SCREENER / "data_loader.py")
    assert "load_market_data" in funcs
    # 只读/标准化：不得包含候选生成、打分、池划分、编排函数
    assert not (funcs & {"prefilter", "_score_codes", "_soft_setup_row", "run_scan", "split_pools", "attach_trade_cards"})


def test_prefilter_is_candidate_set():
    funcs = _defined_funcs(SCREENER / "prefilter.py")
    assert "prefilter" in funcs
    # 候选集合：不得包含打分、信号检测、编排函数
    assert not (funcs & {"_score_codes", "_soft_setup_row", "_detect_on_codes", "run_scan", "apply_box_ladder"})


def test_evaluator_is_single_symbol_result():
    funcs = _defined_funcs(SCREENER / "evaluator.py")
    expected = {"apply_box_ladder", "_score_codes", "_soft_setup_row", "_theme_soft_fill", "observed_signal", "_detect_on_codes"}
    assert expected <= funcs
    # 单标的结果：不得包含数据加载、候选集合、整体编排
    assert not (funcs & {"run_scan", "prefilter", "load_market_data"})


def test_orchestrator_owns_run_scan_only_aggregation():
    funcs = _defined_funcs(SCREENER / "orchestrator.py")
    assert "run_scan" in funcs
    # 编排：不得重复实现单标的评分/候选/数据加载
    assert not (funcs & {"_score_codes", "_soft_setup_row", "_theme_soft_fill", "prefilter", "load_market_data", "_detect_on_codes", "apply_box_ladder"})


def test_facade_is_public_shim_below_350_lines():
    facade = ROOT / "ab_screener" / "run_screener.py"
    lines = facade.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 350, f"facade {len(lines)} 行 ≥ 350"
    # main 保留在 facade；run_scan/load_market_data 由运行时转发断言覆盖
    assert "main" in _defined_funcs(facade)


def test_facade_forwards_public_entries():
    """公共入口可从 facade 导入（scan_job_runner / audit_funnel / 旧测试的兼容路径）。"""
    import ab_screener.run_screener as f

    for name in (
        "run_scan",
        "load_market_data",
        "prefilter",
        "apply_box_ladder",
        "_score_codes",
        "_soft_setup_row",
        "_theme_soft_fill",
        "_detect_on_codes",
        "observed_signal",
        "detect_accumulation_breakout",
    ):
        assert callable(getattr(f, name)), f"facade 未转发 {name}"


def test_root_shim_re_exports_public_names():
    """根 run_screener.py 保持薄 re-export，旧 import（含下划线私有名）不破。"""
    import run_screener as shim

    for name in ("run_scan", "prefilter", "_soft_setup_row", "_theme_soft_fill", "load_market_data", "detect_accumulation_breakout"):
        assert callable(getattr(shim, name)), f"根 shim 未暴露 {name}"


def test_old_imports_still_work():
    code = (
        "from run_screener import prefilter, run_scan, _soft_setup_row, apply_box_ladder, load_market_data; "
        "from ab_screener.run_screener import run_scan as rs2; "
        "import run_screener; print('ok')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_facade_source_does_not_duplicate_business_logic():
    """facade 只转发，不得再定义打分/主题补全/信号检测实现（重复即职责泄漏）。"""
    facade = ROOT / "ab_screener" / "run_screener.py"
    src = facade.read_text(encoding="utf-8")
    for dup in ("def _score_codes(", "def _soft_setup_row(", "def _theme_soft_fill(", "def _detect_on_codes(", "def prefilter("):
        assert dup not in src, f"facade 重复定义了业务函数 {dup}"


def test_orchestrator_has_no_os_kill_ppid_probe():
    """Windows 取消护栏：编排不得使用 os.kill(ppid, 0)。"""
    src = (SCREENER / "orchestrator.py").read_text(encoding="utf-8")
    assert "os.kill(ppid, 0)" not in src
    assert "os.kill(" not in src
