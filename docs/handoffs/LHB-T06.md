# LHB-T06 Handoff — 行为特征、风格分类和协同网络

> 研究 overlay。不产生订单，不打开生产旗标。

## 1. 身份

- 任务 ID：T06
- 前置：T01–T05 已验收（`docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`）
- 时间：2026-08-29
- 交付 commit：无（未要求提交）

## 2. 范围

新增：

- `ab_screener/features/lhb_features.py`
- `ab_screener/research/seat_style.py`
- `ab_screener/research/seat_network.py`
- `tests/test_lhb_features.py`
- `tests/test_seat_style.py`

修改：`scripts/run_lhb_pytest.ps1`、清单 T06 勾选与测试文件列表。

未改 API/UI（T10）、未改 A 池、未写生产库。

## 3. 设计

- 特征窗口 20/60/120/250；`select_pit_facts` 只保留 `available_at <= as_of` 的最大 revision。
- 样本不足返回 `INSUFFICIENT_SAMPLE`，`features/probs` 为 None。
- 风格五类概率 L1 归一化，模型版本 `lhb-style-v1`。
- 共现网络按 `actor_id` 去重，同 actor 多席位独立票数计 1。
- 漂移用总变差距离；任一半样本不足则不报警。

## 4. 测试

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_lhb_features.py tests/test_seat_style.py -q
# 8 passed
```

完整 LHB 明确文件列表（含 T06）上次 102 passed（含 T01–T05 回归）。

## 5. 回滚

删除上述新增模块与测试；还原 pytest 脚本文件列表。

## 6. 自评

- 建议判定：待验收
- 后续：T07 画像快照与小样本收缩
- 未宣布 PERSONAL_INSTITUTIONAL_READY，不可实盘跟单
