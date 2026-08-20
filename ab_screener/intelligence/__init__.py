"""市场情报领域：个股档案、时间线、市场宽度、数据源状态。

契约（implementation P1.4）：
- 本包只读：不创建信号、不产生订单、不写数据库。
- 时间线事件携带 available_at（PIT：何时知晓）；修订后快照指纹变化 → 缓存失效。
- 新闻/社交情绪暂不进入正式特征（无原文归档与 available_at 验收）。
"""
from __future__ import annotations
