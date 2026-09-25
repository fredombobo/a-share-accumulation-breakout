"""日跑推送：未配置零动作、只允许 HTTPS、脱敏、失败不影响退出码、各平台载荷格式。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ab_screener.operations import daily_notify
from ab_screener.operations.daily_notify import notify, payload_for, read_env_file

HOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook-secret-key"


class Recorder:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict, float]] = []
        self.status = status
        self.error = error

    def __call__(self, url: str, payload: dict, timeout: float) -> int:
        self.calls.append((url, payload, timeout))
        if self.error:
            raise self.error
        return self.status


def test_not_configured_does_nothing() -> None:
    sender = Recorder()
    assert notify(1, env={}, sender=sender) == {"status": "NOT_CONFIGURED"}
    assert sender.calls == []


def test_success_is_silent_unless_opted_in() -> None:
    sender = Recorder()
    env = {"AB_NOTIFY_WEBHOOK_URL": HOOK, "AB_NOTIFY_WEBHOOK_KIND": "wecom"}
    assert notify(0, env=env, sender=sender)["status"] == "SKIPPED_SUCCESS"
    assert notify(0, env={**env, "AB_NOTIFY_ON_SUCCESS": "1"}, sender=sender)["status"] == "SENT"
    assert "✅" in sender.calls[0][1]["text"]["content"]


@pytest.mark.parametrize("url", ["http://hooks.example/x", "https://user:pw@hooks.example/x", "ftp://x"])
def test_insecure_webhook_is_refused_without_sending(url: str) -> None:
    sender = Recorder()
    result = notify(1, env={"AB_NOTIFY_WEBHOOK_URL": url}, sender=sender)
    assert result["status"] == "CONFIG_ERROR"
    assert sender.calls == []


def test_unknown_kind_is_refused() -> None:
    result = notify(1, env={"AB_NOTIFY_WEBHOOK_URL": HOOK, "AB_NOTIFY_WEBHOOK_KIND": "sms"}, sender=Recorder())
    assert result["status"] == "CONFIG_ERROR"


def test_failure_message_redacts_token_and_hook_and_keeps_log_tail(tmp_path: Path) -> None:
    log = tmp_path / "daily_task_x.log"
    log.write_text(
        "\n".join(f"line {i}" for i in range(30))
        + "\n数据网关错误 token=real-tushare-token-123 key=hook-secret-key\n",
        encoding="utf-8",
    )
    sender = Recorder()
    env = {
        "AB_NOTIFY_WEBHOOK_URL": HOOK,
        "AB_NOTIFY_WEBHOOK_KIND": "dingtalk",
        "TUSHARE_TOKEN": "real-tushare-token-123",
    }
    result = notify(1, env=env, log_path=log, host="PC-1", sender=sender)

    assert result == {"status": "SENT", "http_status": 200}
    content = sender.calls[0][1]["text"]["content"]
    assert content.startswith("❌ AB 日跑失败（退出码 1） · PC-1")
    assert "real-tushare-token-123" not in content
    assert "hook-secret-key" not in content
    assert "line 29" in content and "line 5" not in content
    assert sender.calls[0][2] == 10.0


def test_send_failure_is_reported_not_raised() -> None:
    sender = Recorder(error=OSError("connection reset key=hook-secret-key"))
    result = notify(1, env={"AB_NOTIFY_WEBHOOK_URL": HOOK}, sender=sender)
    assert result["status"] == "SEND_FAILED"
    assert "hook-secret-key" not in result["error"]
    assert notify(1, env={"AB_NOTIFY_WEBHOOK_URL": HOOK}, sender=Recorder(status=500))["status"] == "SEND_FAILED"


def test_payload_shapes_per_platform() -> None:
    assert payload_for("wecom", "t", "x") == {"msgtype": "text", "text": {"content": "t\nx"}}
    assert payload_for("dingtalk", "t", "x") == {"msgtype": "text", "text": {"content": "t\nx"}}
    assert payload_for("feishu", "t", "x") == {"msg_type": "text", "content": {"text": "t\nx"}}
    assert payload_for("generic", "t", "x") == {"title": "t", "text": "x"}


def test_env_file_reads_only_notify_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "﻿AB_NOTIFY_WEBHOOK_URL='https://h.example/x'\nOPENAI_API_KEY=abc\n# AB_NOTIFY_ON_SUCCESS=1\n",
        encoding="utf-8",
    )
    assert read_env_file(env_file) == {"AB_NOTIFY_WEBHOOK_URL": "https://h.example/x"}


def test_cli_always_exits_zero_even_when_misconfigured(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AB_NOTIFY_WEBHOOK_URL=http://insecure.example/\n", encoding="utf-8")
    monkeypatch.delenv("AB_NOTIFY_WEBHOOK_URL", raising=False)
    assert daily_notify.main(["--exit-code", "1", "--env-file", str(env_file)]) == 0
    assert "CONFIG_ERROR" in capsys.readouterr().out


def test_daily_task_invokes_notifier_without_touching_exit_code() -> None:
    script = (Path(__file__).resolve().parents[1] / "daily_task.ps1").read_text(encoding="utf-8-sig")
    call = script.index("ab_screener.operations.daily_notify --exit-code $code --log $log")
    assert call < script.index("exit $code")
    assert "$code =" not in script[call:]


def test_feishu_hook_id_in_path_is_redacted() -> None:
    hook = "https://open.feishu.cn/open-apis/bot/v2/hook/0123456789abcdef-feishu"
    sender = Recorder(error=OSError("POST /open-apis/bot/v2/hook/0123456789abcdef-feishu failed"))
    result = notify(1, env={"AB_NOTIFY_WEBHOOK_URL": hook, "AB_NOTIFY_WEBHOOK_KIND": "feishu"}, sender=sender)
    assert "0123456789abcdef-feishu" not in result["error"]


def test_webhook_redirect_is_refused() -> None:
    import urllib.error
    import urllib.request

    handler = daily_notify._NoRedirect()
    request = urllib.request.Request(HOOK, method="POST")
    with pytest.raises(urllib.error.HTTPError, match="重定向"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://evil.example/")
