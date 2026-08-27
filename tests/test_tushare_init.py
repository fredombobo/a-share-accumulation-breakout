"""Tushare 统一初始化入口的契约测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

import tushare_init


def test_project_env_is_authoritative_and_url_keeps_trailing_slash(
    tmp_path: Path, monkeypatch,
) -> None:
    """项目配置文件必须覆盖父进程残留值，避免继续使用旧凭据。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TUSHARE_TOKEN=file-token-value\n"
        "TUSHARE_HTTP_URL=https://example.test/\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tushare_init, "_ENV_PATH", env_file)
    monkeypatch.setenv("TUSHARE_TOKEN", "stale-process-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://stale.test")

    assert tushare_init.resolve_token() == "file-token-value"
    assert tushare_init.resolve_http_url() == "https://example.test/"


def test_plaintext_gateway_is_rejected_without_network_access() -> None:
    with pytest.raises(tushare_init.DataTransportSecurityError, match="https://"):
        tushare_init.init_pro(token="test-token", http_url="http://example.test/")


def test_gateway_url_must_not_embed_credentials() -> None:
    with pytest.raises(tushare_init.DataTransportSecurityError, match="内嵌凭据"):
        tushare_init.init_pro(
            token="test-token", http_url="https://user:password@example.test/"
        )


def test_project_sources_use_tushare_init_as_the_only_entrypoint() -> None:
    """业务模块不得绕回兼容层或自行调用 ts.pro_api。"""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "node_modules"} for part in path.parts):
            continue
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        if path.name in {"tushare_init.py", "tushare_http.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        if "from tushare_http import" in source or "ts.pro_api(" in source:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_gateway_query_retries_transient_non_json_response(monkeypatch) -> None:
    """网关偶发空/非 JSON 响应时应在适配器边界有界重试。"""
    class FakeResponse:
        def __init__(self, text: str, status_code: int = 200) -> None:
            self.text = text
            self.status_code = status_code

        def __bool__(self) -> bool:
            return True

    responses = iter([
        FakeResponse("<html>temporary gateway error</html>"),
        FakeResponse(
            '{"code":0,"data":{"fields":["cal_date","is_open"],'
            '"items":[["20260807",1]]}}'
        ),
    ])
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(tushare_init.crequests, "post", fake_post)
    monkeypatch.setattr(tushare_init.time, "sleep", lambda _seconds: None)
    pro = tushare_init.init_pro(token="test-token", http_url="https://example.test/")

    result = pro.query("trade_cal")

    assert len(calls) == 2
    assert all(call[0].startswith("https://") for call in calls)
    assert all(call[1]["verify"] is True for call in calls)
    assert all(call[1]["allow_redirects"] is False for call in calls)
    assert result.to_dict("records") == [{"cal_date": "20260807", "is_open": 1}]


def test_gateway_redirect_is_rejected_to_prevent_tls_downgrade(monkeypatch) -> None:
    class RedirectResponse:
        text = ""
        status_code = 302

    monkeypatch.setattr(
        tushare_init.crequests, "post", lambda _url, **_kwargs: RedirectResponse()
    )
    monkeypatch.setattr(tushare_init.time, "sleep", lambda _seconds: None)
    pro = tushare_init.init_pro(token="test-token", http_url="https://example.test/")

    with pytest.raises(RuntimeError, match="拒绝重定向"):
        pro.query("trade_cal")
