# ADR-021 — v2 就绪闸门（readiness gates）

状态：接受（P0 冻结） ｜ 日期：2026-08-17

## 上下文
机构化交付需要机器可判定的完成状态，避免「页面完成=完成」误判。

## 决策
- 七闸门：D 数据 / R 研究 / S 语义 / P 性能 / L 账本 / O 运维 / G 总验收
- 判定（ab_screener/domain/readiness.py）：
  - 硬门（D/S/P/L/O/G）任一失败 → BLOCKED
  - 仅 R 失败且其它全 PASS → ENGINEERING_READY_RESEARCH_BLOCKED（工程就绪、研究阻断）
  - 全部 PASS → OK
- 证据输入由 capture_v2_baseline 与各阶段验收提供；worktree_dirty / identity 不匹配自动 BLOCKED

## 后果
正向：诚实交付；逆向：必须为每闸门提供机器证据。
