# 龙虎榜定向测试（PowerShell 不展开 glob，必须列出文件）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = ".\.venv312\Scripts\python.exe"
& $py -m pytest `
  tests/test_lhb_contracts.py `
  tests/test_lhb_migrations.py `
  tests/test_lhb_source_adapters.py `
  tests/test_lhb_normalization.py `
  tests/test_lhb_reconciliation.py `
  tests/test_lhb_backfill_resume.py `
  tests/test_lhb_real_shape.py `
  tests/test_seat_identity.py `
  tests/test_lhb_features.py `
  tests/test_seat_style.py `
  tests/test_lhb_profiles.py `
  tests/test_lhb_signal_engine.py `
  tests/test_lhb_event_study.py `
  tests/test_lhb_backtest_no_lookahead.py `
  tests/test_lhb_api_contract.py `
  tests/test_lhb_daily_dag.py `
  tests/test_lhb_alerts.py `
  tests/test_lhb_overlay_boundaries.py `
  tests/test_lhb_readiness.py `
  tests/test_lhb_product_pipeline.py `
  tests/test_openapi_contract_v2.py `
  -q
exit $LASTEXITCODE
