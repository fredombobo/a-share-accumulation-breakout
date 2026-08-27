from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

import scripts.check_vendor_tls as vendor_tls
import tushare_init


def test_probe_uses_https_business_api_without_leaking_token(monkeypatch):
    secret = "never-write-this-token"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://vendor.example/api")
    monkeypatch.setattr(
        vendor_tls,
        "_certificate_metadata",
        lambda *_: {
            "scheme": "https",
            "host": "vendor.example",
            "certificate_verified": True,
            "hostname_verified": True,
            "tls_version": "TLSv1.3",
        },
    )
    pro = SimpleNamespace(
        trade_cal=lambda **_: pd.DataFrame(
            [{"exchange": "SSE", "cal_date": "20260827", "is_open": 1}]
        )
    )
    monkeypatch.setattr(tushare_init, "init_pro", lambda **_: pro)

    report = vendor_tls.run_probe(env_file=None)

    assert report["status"] == "PASS"
    assert report["endpoint"]["scheme"] == "https"
    assert report["token_in_report"] is False
    assert secret not in json.dumps(report, ensure_ascii=False)


def test_certificate_probe_rejects_plain_http():
    try:
        vendor_tls._certificate_metadata("http://vendor.example", 1)
    except ValueError as exc:
        assert "https://" in str(exc)
    else:  # pragma: no cover - fail clearly if fail-closed validation regresses
        raise AssertionError("plain HTTP was accepted")
