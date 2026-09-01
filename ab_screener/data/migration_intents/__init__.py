"""迁移意图包：导入即注册全部**生产**意图（migrate_v2.py 依赖本包侧效应）。

龙虎榜（``v2:lhb_tracking`` / ``v2:lhb_ops``）是刻意的例外。按产品边界，它只在
隔离副本 ``lhb_product.db`` 上使用，生产库 ``runtime/stock_data.db`` 从不建这些表。
若在此默认注册，8001 启动时的 schema 断言会把它们判为 MIGRATION_PENDING 并拒绝启动，
逼着去迁移生产库——那正是本项目明令禁止的操作。

需要龙虎榜 schema 的入口（副本准备 / 8123 服务 / 盘后流水线 / 副本维护 / 测试）
必须显式调用 :func:`register_lhb_intents`。
"""
from ab_screener.data.migration_intents import (
    aux_history_v2,  # noqa: F401
    corporate_action_pit_v2,  # noqa: F401
    corporate_actions_v2,  # noqa: F401
    execution_lineage_v2,  # noqa: F401
    instrument_history_v2,  # noqa: F401
    operations_v2,  # noqa: F401
    pit_history_v2,  # noqa: F401
    portfolio_risk_v2,  # noqa: F401
    research_governance_v2,  # noqa: F401
    review_v2,  # noqa: F401
    scan_profile_v2,  # noqa: F401
    scan_signal_v2,  # noqa: F401
)


def register_lhb_intents() -> None:
    """显式注册龙虎榜迁移意图（只用于隔离副本）。重复调用安全。"""
    from ab_screener.data.migration_intents import lhb_ops_v2, lhb_tracking_v2

    lhb_tracking_v2.register_lhb_tracking_migration()
    lhb_ops_v2.register_lhb_ops_migration()
