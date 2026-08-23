"""迁移意图包：导入即注册全部意图（migrate_v2.py 依赖本包侧效应）。"""
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
