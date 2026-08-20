# DATA-DICTIONARY-PIT-V2 — PIT 历史数据字典（P1.1）

> 契约版本：v2 · 2026-08-18 · 对应迁移 `v2:pit_history`

## 1. PIT 五元组（`ab_screener/domain/data_point.PitRecord`）

每条历史事实保存为五元组：

| 字段 | 类型 | 说明 |
|------|------|------|
| `business_key` | dict[str,str] | 数据集业务键（如 daily 为 `ts_code+trade_date`），全部字符串化 |
| `revision` | int ≥1 | 同一业务键的第几次修订；每次再写入 +1，旧版本保留 |
| `available_at` | str ISO +08:00 | 该版本**真实可用时刻**（入库时刻，非数据日期） |
| `source` | str | 数据来源（如 `tushare`） |
| `content_hash` | str sha256[:16] | 规范化 payload 的哈希，抽样核对用 |

**fail-closed**：缺 `available_at/source/revision`、时间无时区、业务键缺失 → 一律拒绝写入；
回填数据不得伪装成历史可用（`available_at` 是入库时刻而非行情日期）。

## 2. 历史表（append-only）

| 表 | 业务键 | 说明 |
|----|--------|------|
| `daily_history` | ts_code, trade_date | 日线 OHLCV |
| `daily_basic_history` | ts_code, trade_date | 基本面（pe/pb/mv/turnover） |
| `moneyflow_history` | ts_code, trade_date | 个股资金流 |
| `fina_indicator_history` | ts_code, ann_date | 财务指标（按公告日） |
| `stock_basic_history` | ts_code | 股票列表快照 |
| `adj_factor_history` | ts_code, trade_date | 复权因子 |

列：业务键列（TEXT NOT NULL） + `revision` + `available_at` + `source` +
`content_hash` + `payload_json`。主键 = 业务键 + revision。

**硬约束**：全部历史表注册 `BEFORE UPDATE` / `BEFORE DELETE` 触发器
（`RAISE(ABORT, '<table> is append-only')`），任何覆盖/删除在数据库层被拒绝。

## 3. 写入与清单

- `ab_screener/data/pit_writer.py`：`write_plain` / `build_records` + `write_chunk`
  （单事务 ≤ `MAX_ROWS_PER_TX=50_000`）。
- 每次写入登记 `raw_ingest_manifests`：
  `(manifest_id, dataset, partition_key, source, available_at, row_count, content_sha256, ingested_at)`。

## 4. As-of 读取

- `ab_screener/data/pit_repository.PitRepository.read_asof(dataset, business_key, decision_at)`：
  返回 `available_at <= decision_at` 中 revision 最大者（决策时点应读取的版本）。
- 同一业务键两次修订，修订前后 `decision_at` 分别返回旧/新版本（见
  `tests/test_pit_repository.py::test_asof_switches_revision_old_to_new`）。

## 5. Tushare 适配器（`ab_screener/data/adapters/tushare_pit.py`）

- **禁止裸 requests / 第二套 Token/URL 初始化**：真实调用只走根
  `tushare_init`（`from tushare_init import pro`）；离线测试可注入 fake `pro`。
- `df_to_pit_rows(df, dataset)`：业务键字符串化；缺键列拒绝。

## 6. 回填（`ab_screener/application/pit_backfill.py` + `scripts/backfill_pit_v2.py`）

- 分区：daily 族按交易日、`fina_indicator` 按公告月、`stock_basic` 单块全量。
- 每分区写前登记 `pit_backfill_checkpoints(status='in_progress')`，成功置 `done`；
  中断后从最后一个未完成分区续跑（`done` 分区跳过）。
- 命令：
  ```text
  .venv312\Scripts\python.exe scripts\backfill_pit_v2.py --db <绝对路径副本.db> --preflight
  .venv312\Scripts\python.exe scripts\backfill_pit_v2.py --db <绝对路径副本.db> --run --start YYYYMMDD --end YYYYMMDD --datasets daily daily_basic
  .venv312\Scripts\python.exe scripts\backfill_pit_v2.py --db <绝对路径副本.db> --coverage
  ```
- 开始前要求：已验证备份、维护窗口、目标绝对路径、可用空间 ≥ 2×当前 DB + 预计新增、WAL 预算。
- `coverage_report()`：各数据集已回填分区/行数；`all_done=True` 且抽样 hash 100% 通过后
  才允许翻转 `V2_PIT_READ_ENABLED=true`（P1.2+ 门禁使用）。

## 7. 时间约定

- 全部时间统一 `+08:00`；写入（`available_at`）与 as-of 读取（`decision_at`）均归一化。
- 无时区输入按 Asia/Shanghai 解释（`normalize_ts`）。

## 8. 已知边界（诚实声明）

- 真实 517 万行历史回填必须走维护窗口 + 绝对路径副本（本仓库 `runtime/` 不入库）；
  仓库内只交付契约、实现与离线测试，不交付回填后的数据。
- `done` 分区数据源变化不会自动重写；需要显式重置 checkpoint（未来维护命令）。
- fina_indicator 精确拉取参数（period 语义）在真实维护窗口首次运行中校验。
