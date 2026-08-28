# V2 R 闸门：研究/生产市场状态一致性修复

## 1. 背景与结论边界

生产扫描已经使用 `000300.SH` 的 MA20 与 20 日收益划分进攻、中性和防守状态，并在防守状态禁止新增 A 池开仓。当前权威研究未应用该入口约束，导致研究信号集合与生产可执行信号集合不一致。

本工作包只修复语义漂移，不降低 `ROBUST_PERSONAL_V2` 的 PBO、DSR、MinTRL、嵌套 WF、2×成本或参数邻域阈值。旧权威报告 `v2auth20260828f` 保持不可变 FAIL；新运行是披露既往结果后的新假设验证，不能被描述为全新未触碰 holdout。

## 2. 实施计划与影响文件

1. 在 `ab_screener/market_regime.py` 暴露版本化纯分类函数及配置哈希，生产扫描继续复用同一函数。
2. 在 `ab_screener/research/regime_filter.py` 基于冻结的 CSI300 PIT 行情逐日因果计算允许开仓日期；缺日期、缺前置窗口或非法行情均 fail-closed。
3. 扩展 `ResearchPitSnapshot`，将基准代码、逐修订行情、SHA-256 和行数绑定到数据身份；权威快照强制包含 `000300.SH`。
4. 将同一允许日期集合传入 IS、OOS、WF、正式组合回放、随机基线、MA20/60 基线和 2×成本压力；信号日 `t` 仅使用截至 `t` 的基准收盘。
5. 预登记请求和可信报告写入市场状态版本、策略哈希、基准哈希、允许/阻断日期数和日期集合哈希。
6. 增加 `scripts/sync_benchmark_pit_history.py`，通过项目唯一 HTTPS Tushare 初始化入口补齐缺失的 CSI300 PIT 历史；实际抓取使用真实 `available_at`，重复运行幂等，不输出 Token。
7. 完成离线门禁后再维护生产数据库、重新运行真实数据门、预登记新 A 策略研究并重建七闸门。

主要影响文件：

- `ab_screener/market_regime.py`
- `ab_screener/research/{pit_reader,regime_filter,trusted_run,backtest_engine,baselines}.py`
- `optimizer.py`、`walkforward.py`
- `ab_screener/data/benchmark_pit_sync.py`
- `ab_screener/api/routers/legacy_lab.py`
- `scripts/{run_trusted_research_real,sync_benchmark_pit_history}.py`
- 对应单元、集成与防未来函数测试

## 3. 验收标准

- 同一 `(close, MA20, ret20)` 在研究和生产返回完全相同的状态与 `allow_new_entries`。
- 防守日的候选策略、随机基线和 MA 基线均不得产生新入场；中性/进攻日不被该门禁误删。
- 修改信号日之后的基准行情，不得改变该信号日的允许结果。
- 股票交易日缺少对应 CSI300 PIT 行情、前置 25 行不足或 PIT 修订在 `decision_at` 后才可见时，权威研究拒绝启动。
- PIT 补齐在写入前验证供应商日期全集；供应商缺日时零写入；成功后每个目标日存在 canonical recovery 与真实供应商观察证据，重跑不新增修订。
- 新报告绑定代码、数据库、股票宇宙、CSI300、市场状态策略、组合、成本和执行模型身份。
- 新研究仍须原样通过全部正式晋级门；失败则 R 继续 FAIL，不创建候选、不进入 A 池、不生成订单。
- `LIVE_TRADING_ENABLED=false`，不增加券商或真实下单能力。

## 4. 发布、回滚与时序约束

- 代码先在隔离工作树通过相关测试及 `scripts/quality_gate.ps1`，再合入 `v2r-final-integration`。
- 数据维护前必须已有可验证备份；维护完成后重新生成真实数据门报告和数据库指纹。
- 若代码回滚，删除新的权威 ID 绑定即可恢复旧研究读取路径；已经写入的 PIT 历史为追加式来源证据，不删除、不改写。
- S 闸门和 O 闸门仍只接受不同的真实交易日证据。2026-08-28 当天不得重复生成 soak 或备份冒充新增天数。
