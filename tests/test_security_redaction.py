from __future__ import annotations

from ab_screener.security import redact_sensitive_text


def test_redacts_vendor_echo_and_common_credential_forms() -> None:
    credential = "A" * 48
    samples = (
        f"token不对，您传过来的是{credential}请确认",
        f"TUSHARE_TOKEN={credential}",
        f'{{"access_token":"{credential}"}}',
        f"Bearer {credential}",
    )

    for sample in samples:
        result = redact_sensitive_text(sample)
        assert credential not in result
        assert "[REDACTED]" in result


def test_redacts_known_secret_without_over_redacting_hashes() -> None:
    secret = "s" * 32
    unrelated_hash = "f" * 64

    result = redact_sensitive_text(
        f"request failed secret={secret}; digest={unrelated_hash}",
        known_secrets=(secret,),
    )

    assert secret not in result
    assert unrelated_hash in result
