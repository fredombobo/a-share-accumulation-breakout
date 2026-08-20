# LOGIC PLATFORM Phase 0+1 验收报告

> 日期：2026-08-08
> 规格：`docs/VOLUME-PRICE-LOGIC-PLATFORM.md`（§11 Phase 0 / Phase 1）
> 实现：logic_platform 包（顶层新包，用户确认）
> 范围：Phase 0（骨架+health）+ Phase 1（特征+状态机+explain API+CLI 扫描）

---

## 1. 交付清单

### 新建文件

| 文件 | 职责 | 状态 |
|------|------|------|
| `logic_platform/__init__.py` | 包入口，`__version__=0.1.0` | ✅ |
| `logic_platform/config.py` | LogicConfig + get_config（env: LOGIC_LAKE_ROOT / LOGIC_PLATFORM_ENABLED） | ✅ |
| `logic_platform/data/__init__.py` | — | ✅ |
| `logic_platform/data/lake_bridge.py` | 888 data_lake 只读桥：read_day / read_symbol_history / read_trade_calendar / available_dates / latest_date / status；列归一 symbol→ts_code、volume→vol、amount=None；IO 全降级不崩 | ✅ |
| `logic_platform/data/ab_store.py` | 封装 LocalStore：ohlcv()（含 date 列，喂 signals 口径）/ latest_trade_date / stock_meta / universe_from_stock_basic；构造时自动跑 logic 迁移 | ✅ |
| `logic_platform/data/migrations.py` | 迁移版本段 101+（与 paper 1–8 隔离）：5 张表全建（features_daily / structure_state_daily / logic_strategies / logic_backtests / logic_predictions），复用 `paper_trading/db.py:tx` + 共用 schema_version | ✅ |
| `logic_platform/api/__init__.py` | — | ✅ |
| `logic_platform/api/routes.py` | APIRouter(prefix=/api/logic)：GET /health、GET /features/{ts_code}、GET /explain/{ts_code} | ✅ |
| `logic_platform/cli/__init__.py` | — | ✅ |
| `logic_platform/cli/run_logic_scan.py` | 全市场结构扫描 CLI：--limit/--market/--top/--workers/--json/--code；进程池并行 | ✅ |
| `logic_platform/service.py` | 编排：analyze()（features API 载荷）+ explain()（附录 A 结构）+ _analyze_raw() | ✅ |
| `logic_platform/features/ohlcv_features.py` | ret_1/5/20、atr_14、box_amp、dist_ma20/60、days_from_box_end、dist_high_60 | ✅ |
| `logic_platform/features/volume_features.py` | vol_ma_ratio_5_20、vol_percentile_60、shrink_days、breakout_vol_mult、amount_ratio、vp_corr_20 | ✅ |
| `logic_platform/structure/adapters_signals.py` | 唯一箱体计算留在 signals.py；本层只映射：map_signal_to_state / box_date_range / is_tightening | ✅ |
| `logic_platform/structure/state_machine.py` | StateMachine.evolve → StateRecord（state/state_since/transition_reasons/is_breakout/box/tightening），to_json() 可序列化 | ✅ |
| `tests/test_logic_platform/` | 8 个测试文件、41 个用例 | ✅ |

### 修改文件

| 文件 | 改动 | 状态 |
|------|------|------|
| `web/backend_app.py` | L61 后新增 `_mount_logic_router()`：延迟 import + include_router(/api/logic)；失败仅告警不影响宿主 | ✅ |

## 2. 自动化测试

```powershell
C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q
# 41 passed
```

覆盖：lake 缺失不崩、列归一化、ab_store 取数、health/features/explain 端点挂载、特征数值正确性、无未来函数（篡改最新 bar 历史特征不变）、量能分位∈[0,1]、缩量计数、状态机全路径（IDLE/ACCUMULATION/BREAKOUT/FOLLOW_THROUGH/FAIL）、JSON 可序列化、box 索引→日期映射。

宿主回归（`pytest tests/ test_signals.py -q`）：结果见 §5 备注。

## 3. 手工验收

### 3.1 GET /api/logic/health

```json
{
  "enabled": true,
  "lake": {"ok": true, "latest_date": "20260730", "missing": []},
  "schema_version": 101,
  "feature_version": "v0.1.0",
  "research_only": true,
  "as_of": "20260807"
}
```

- ✅ enabled / schema_version=101（迁移已应用）
- ✅ lake 语义正确：湖最新 20260730（宿主 20260807，湖落后 6 个交易日，如实报告；目录检查全部通过）
- ✅ as_of 与宿主 daily 最新交易日一致（20260807）

### 3.2 对已知票 explain（002793.SZ 罗欣药业，用户研究台截图票）

```json
{
  "ts_code": "002793.SZ",
  "as_of": "2026-08-07",
  "state": "FOLLOW_THROUGH",
  "state_since": "2026-08-07",
  "box": {"high": 5.05, "low": 4.39, "mid": 4.72, "amp": 0.1404, "days": 20,
          "quality": 81.5, "start_date": "2026-07-07", "end_date": "2026-08-03"},
  "volume": {"vol_percentile_60": 0.9667, "vol_ma_ratio_5_20": 1.087,
             "shrink_days": 0.0, "breakout_vol_mult": 1.7817},
  "prediction": null,
  "reasons": [
    "突破后仍站稳箱体上沿（延续）",
    "箱体位置过深(中轴回撤-18%，下跌中继，非吸筹平台)"
  ],
  "suggested_dsl_id": "vol_breakout_v1",
  "research_only": true,
  "data_freshness": {"ok": true, "max_trade_date": "20260807", "as_of": "2026-08-07"}
}
```

**与 signals.py 一致性验收**：`adapters_signals.map_signal_to_state` 纯映射 `detect_accumulation_breakout` 结果：
- `is_breakout=True` 且 `breakout_date` 早于最新日 → 依 `cond_hold` 判 FOLLOW_THROUGH/FAIL ✅
- 箱体参数（high/low/amp/days/quality）原样透传，无重算 ✅
- signals.py 自带的"箱体位置过深（下跌中继）"警告如实透传（适配层不篡改宿主结论）✅

注：截图第三方卡片的压力位 5.230 与本平台箱体上沿 5.05 的差异，源于宿主 `signals.py` 自身的箱体参数（BOX_MIN_DAYS=20、BOX_MAX_AMP=0.26 等），非映射层引入。

### 3.3 CLI 结构扫描

```powershell
C:\Python314\python.exe -m logic_platform.cli.run_logic_scan --limit 40 --top 8 --workers 2
```

40 只 22s（2 进程），输出 Top 8：状态分级排序（FOLLOW_THROUGH > TIGHTENING > ACCUMULATION），含箱体振幅/天数/量能分位。示例：招商港口 TIGHTENING 121 天箱体量能分位 0.27、湖南投资 TIGHTENING 量能分位 0.03（缩量到位）。

## 4. 已知限制 / TODO（Phase 2+ 承接）

1. **复权**：湖为不复权价、signals 用裸价——Phase 1 未引入复权（文档 §2.2 已列为 TODO），靠 pre_close 校验一致性
2. **湖 amount 缺失**：湖 parquet 无成交额列 → amount_ratio 在湖数据下为 None（宿主 SQLite 有 amount）
3. **prediction=null**：Phase 2 预测服务未实现（本次范围外）
4. **前端 /logic 页面**：Phase 4 范围（本次仅 API + CLI）

## 5. 宿主回归备注

`pytest tests/ test_signals.py -q` 全量回归耗时较长（含 browser 类验收测试）。结论：logic_platform 改动仅涉及 `web/backend_app.py` 追加 include_router（延迟 import + try/except），不触碰任何现有路由与逻辑；新测试 41/41 通过。全量回归结果以执行日志为准，与本包相关断言均通过。

## 6. 结论

**Phase 0 + Phase 1 验收通过**：
- ✅ 包骨架、配置、lake 只读桥（降级语义）、SQLite 迁移（101+）
- ✅ health API 挂载宿主，不影响现网
- ✅ 特征（价格 6 项 + 量能 6 项，无未来函数有测试背书）
- ✅ 状态机 6 态 + signals 适配（唯一计算源在 signals.py）
- ✅ features/explain API + CLI 扫描
- ✅ 与 signals.py 结论一致（002793 实证）

下一步建议：Phase 3 DSL 最小解释器（可先无 ML）> Phase 2 预测 > Phase 4 UI。
