"""供应商 HTTPS/TLS 独立探针；报告不包含 Token。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TZ = ZoneInfo("Asia/Shanghai")


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_local_env(path: Path | None) -> None:
    if path is None or not path.is_file():
        return
    allowed = {"TUSHARE_TOKEN", "TUSHARE_HTTP_URL"}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in allowed:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _certificate_metadata(url: str, timeout: float) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("供应商 URL 必须是带有效主机名的 https:// 地址")
    port = parsed.port or 443
    context = ssl.create_default_context()
    with (
        socket.create_connection((parsed.hostname, port), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=parsed.hostname) as secured,
    ):
        certificate = secured.getpeercert()
        der = secured.getpeercert(binary_form=True)
        cipher = secured.cipher()
        tls_version = secured.version()
    if tls_version not in {"TLSv1.2", "TLSv1.3"}:
        raise RuntimeError(f"TLS 版本不满足要求: {tls_version}")
    if not isinstance(certificate, dict) or not der:
        raise RuntimeError("供应商 TLS 证书元数据不可用")
    not_after_raw = str(certificate.get("notAfter") or "")
    if not not_after_raw:
        raise RuntimeError("证书缺少 notAfter")
    not_after_epoch = ssl.cert_time_to_seconds(not_after_raw)
    if not_after_epoch > 0 and not_after_epoch <= datetime.now(UTC).timestamp():
        raise RuntimeError("供应商 TLS 证书已过期")
    return {
        "scheme": "https",
        "host": parsed.hostname,
        "port": port,
        "tls_version": tls_version,
        "cipher": cipher[0] if cipher else None,
        "certificate_sha256": hashlib.sha256(der).hexdigest(),
        "certificate_not_after": not_after_raw,
        "hostname_verified": True,
        "certificate_verified": True,
        "redirects_allowed": False,
    }


def run_probe(*, env_file: Path | None, timeout: float = 30) -> dict:
    _load_local_env(env_file)
    from tushare_init import init_pro, resolve_http_url, sanitize_error

    generated_at = datetime.now(_TZ).isoformat(timespec="seconds")
    url = resolve_http_url()
    try:
        tls = _certificate_metadata(url, timeout)
        pro = init_pro(http_url=url, timeout=max(1, int(timeout)))
        frame = pro.trade_cal(
            exchange="SSE",
            start_date=datetime.now(_TZ).strftime("%Y%m%d"),
            end_date=datetime.now(_TZ).strftime("%Y%m%d"),
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        if frame is None or frame.empty:
            raise RuntimeError("HTTPS 业务 API 返回空交易日历")
        payload = {
            "schema": "vendor-transport-evidence-v2",
            "status": "PASS",
            "generated_at": generated_at,
            "endpoint": tls,
            "api_probe": {
                "api": "trade_cal",
                "rows": int(len(frame)),
                "columns": sorted(str(column) for column in frame.columns),
            },
            "token_present": bool(os.environ.get("TUSHARE_TOKEN")),
            "token_in_report": False,
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "schema": "vendor-transport-evidence-v2",
            "status": "FAIL",
            "generated_at": generated_at,
            "endpoint": {"scheme": urlsplit(url).scheme, "host": urlsplit(url).hostname},
            "reason": sanitize_error(exc)[:300],
            "token_present": bool(os.environ.get("TUSHARE_TOKEN")),
            "token_in_report": False,
        }
    payload["evidence_sha256"] = hashlib.sha256(
        _canonical(payload).encode("utf-8")
    ).hexdigest()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="supplier HTTPS/TLS evidence probe")
    parser.add_argument("--env-file")
    parser.add_argument("--report", required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    payload = run_probe(
        env_file=Path(args.env_file).resolve() if args.env_file else None,
        timeout=args.timeout,
    )
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "report": str(report),
                "evidence_sha256": payload["evidence_sha256"],
                "reason": payload.get("reason"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
