# ADR-020 — v2 模块边界

状态：接受（P0 冻结） ｜ 日期：2026-08-17

## 上下文
v2 新增错误码/配置/迁移/就绪模块需落位；避免 backend_app.py 继续膨胀与依赖倒置。

## 决策
- 纯领域逻辑 → ab_screener/domain（errors_v2 / readiness）
- 应用服务/配置 → ab_screener/application（platform_config）
- 持久化/迁移 → ab_screener/data（migration_registry / schema_check）
- HTTP 路由 → ab_screener/api/routers（后续阶段）；backend_app.py 仅装配
- API 层禁止直接 import sqlite3/subprocess（架构测试 P0.3 锁定）

## 后果
正向：可测、可单测；逆向：迁移旧代码有一次性成本。
