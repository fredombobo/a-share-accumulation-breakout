# Nine-Point Personal Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Lab execution, daily evidence, research promotion, the home-page next action, and release evidence form one deterministic and auditable personal workflow.

**Architecture:** Keep the modular monolith. SQLite remains the transaction and evidence store; domain services own lifecycle decisions, append-only manifests, and the trusted gate. FastAPI becomes an adapter and React renders the one server-derived next action without recalculating business rules.

**Tech Stack:** Python 3.12-compatible FastAPI, SQLite/WAL, pandas/process pools, React 19/TypeScript, Pytest, Ruff, Mypy.

---

### Task 1: Lab lifecycle is single-source and cancellable

**Files:**
- Modify: `ab_screener/data/migrations_v2.py`
- Modify: `ab_screener/research/store.py`
- Modify: `optimizer.py`
- Modify: `walkforward.py`
- Modify: `ab_screener/research/trusted_run.py`
- Modify: `web/backend_app.py`
- Test: `tests/test_research_run_store.py`
- Test: `tests/test_lab_task_recovery.py`
- Create: `tests/test_optimizer_cancellation.py`

- [ ] Add a failing store test proving two concurrent `BEGIN IMMEDIATE` start attempts yield one active run.
- [ ] Add a failing optimizer test proving cancellation terminates the pool and raises a typed cancellation error.
- [ ] Add a failing API test proving status is read from SQLite and remains consistent after memory state is cleared.
- [ ] Add migration fields for cancellation/heartbeat/worker identity and one-active-run enforcement after quarantining legacy active rows.
- [ ] Add atomic `create_or_resume` and persisted cancellation methods to `ResearchRunStore`.
- [ ] Thread a cancellation callback through trusted research, IS/OOS, WF, and `optimizer.run_grid`; terminate pool processes on cancellation.
- [ ] Guard start/resume creation with `_LAB_LOCK`; make SQLite authoritative and memory only a runtime handle cache.
- [ ] Verify cancellation reaches `cancelled`, releases the active slot, stops workers, and can resume only from a valid checkpoint.

### Task 2: Immutable daily run manifest

**Files:**
- Create: `ab_screener/application/daily_manifest.py`
- Modify: `ab_screener/data/migrations_v2.py`
- Modify: `paper_trading/settlement.py`
- Modify: `web/backend_app.py`
- Test: `tests/test_daily_run_manifest.py`

- [ ] Add failing tests for deterministic hashes, idempotent recreation, append-only update/delete rejection, and complete source references.
- [ ] Add `daily_run_manifests` plus immutable triggers; store canonical JSON, SHA-256, data/code/config versions and source hashes.
- [ ] Build one manifest from `scan_runs`, `scan_run_candidates`, `pt_signal_snapshot`, `pt_order`, `pt_fill`, `pt_cycle`, and `pt_reconciliation`.
- [ ] Generate the manifest after a successful daily cycle and expose read-only list/detail APIs.
- [ ] Refuse `COMPLETE` status when a required source reference or version is missing; record explicit blockers instead.

### Task 3: Trusted candidate gate includes anti-overfit evidence

**Files:**
- Modify: `ab_screener/research/validation.py`
- Modify: `ab_screener/research/trusted_run.py`
- Modify: `ab_screener/research/reporting.py`
- Modify: `ab_screener/research/store.py`
- Test: `tests/test_trusted_research_gate.py`
- Test: `tests/test_trusted_report.py`

- [ ] Add failing tests showing missing anti-overfit evidence is `INSUFFICIENT_EVIDENCE` and failed PBO/DSR/stability is `FAIL`.
- [ ] Produce deterministic candidate-trial diagnostics from the frozen IS search without consulting OOS for selection.
- [ ] Require net OOS, three WF windows, both baselines, parameter sensitivity and anti-overfit checks for `PASS`.
- [ ] Persist the exact gate version and diagnostics; only a true `PASS` may create an isolated candidate.
- [ ] Run the current full preset and archive its honest PASS/FAIL report without relaxing thresholds.

### Task 4: One correct home-page next action

**Files:**
- Create: `ab_screener/application/today_guide.py`
- Modify: `web/backend_app.py`
- Modify: `web/frontend/src/api/client.ts`
- Modify: `web/frontend/src/pages/Overview.tsx`
- Modify: `web/frontend/src/styles/theme.css`
- Test: `tests/test_today_guide.py`

- [ ] Add failing priority tests for sync data, wait/cancel scan, run scan, resolve reconciliation, create account, review draft, settle, and complete.
- [ ] Derive exactly one action from server state; when ledger earliest date is later than latest quote, return `SYNC_DATA`.
- [ ] Add `GET /api/today` and a single primary action card; remove the competing three-step novice CTA.
- [ ] Route actions to existing screens/endpoints and display blockers in plain Chinese without exposing raw JSON.

### Task 5: Reproducible release governance

**Files:**
- Create: `tasks/backlog.yaml`
- Create: `tasks/implementation_state.yaml`
- Create: `ab_screener/application/release_evidence.py`
- Modify: `paper_trading/real_data_gate.py`
- Modify: `web/backend_app.py`
- Modify: `docs/STATUS.md`
- Test: `tests/test_release_evidence.py`

- [ ] Add failing tests proving a report older than 24 hours, dirty code identity, or mismatched config/build cannot be release-ready.
- [ ] Generate one release evidence record joining git SHA, dirty-tree fingerprint, build version, config hash, database fingerprint and real-data-gate SHA.
- [ ] Expose release readiness separately from runtime freshness; never treat `NOT_RUN` as PASS.
- [ ] Record the five tasks and evidence paths in backlog/state documents.
- [ ] Preserve all pre-existing work, then classify and commit only verified project changes so the release candidate has a clean worktree.

### Task 6: Completion audit

**Files:**
- Modify: `docs/STATUS.md`
- Create: `docs/NINE-POINT-CLOSED-LOOP-ACCEPTANCE-2026-08-11.md`

- [ ] Run focused Pytest suites after every task and the full Pytest suite at the end.
- [ ] Run `python -m ruff check .`, configured Mypy targets, and `npm --prefix web/frontend run build`.
- [ ] Run a fresh real-data gate using the configured adapter; confirm report age is under 24 hours and hashes match the same build/config.
- [ ] Restart the service, verify Lab recovery/cancellation, manifest APIs, one home action, OpenAPI, and ledger invariants.
- [ ] Audit every objective against authoritative files, DB records, process state, browser behavior and command outputs before marking complete.
