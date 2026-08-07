"""纸面交易平台领域模块（paper_trading）

独立领域包：数据库迁移、账户/订单/成交/现金/持仓账本、交易日历、交易规则。
SQLite 仍是唯一事务数据库；本包提供：
  - migrations  : 增量迁移机制（schema_version + 有序迁移列表）
  - schema      : 领域表 DDL 常量与表名白名单
  - db          : BEGIN IMMEDIATE 显式事务上下文
  - cal         : 交易日历（Tushare 优先 + 本地推断兜底）
  - rules       : instrument 级交易规则
  - errors      : 领域错误（code/message/details/retryable）
"""
