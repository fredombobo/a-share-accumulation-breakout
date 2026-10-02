"""日跑结果推送：计划任务结束后把结果发到用户自己配置的 Webhook。

- 只读项目 `.env` 中的 `AB_NOTIFY_*` 键；未配置 Webhook 时什么都不做（退出 0）。
- 仅允许 HTTPS，拒绝重定向；超时 10 秒；推送失败只打印警告，**绝不改变**日跑退出码。
- 消息正文经 `redact_sensitive_text` 脱敏（已知 Token + 凭据模式），只带日志末尾若干行。
- 默认只推送失败；`AB_NOTIFY_ON_SUCCESS=1` 时成功也推送。

`.env` 示例::

    AB_NOTIFY_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
    AB_NOTIFY_WEBHOOK_KIND=wecom        # wecom / dingtalk / feishu / generic
    AB_NOTIFY_ON_SUCCESS=0

用法（daily_task.ps1 自动调用）::

    python -m ab_screener.operations.daily_notify --exit-code 1 --log runtime\\daily_task_x.log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ab_screener.security import redact_sensitive_text

KINDS = ("wecom", "dingtalk", "feishu", "generic")
_ENV_KEYS = (
    "AB_NOTIFY_WEBHOOK_URL",
    "AB_NOTIFY_WEBHOOK_KIND",
    "AB_NOTIFY_ON_SUCCESS",
    "TUSHARE_TOKEN",
)
TAIL_LINES = 12
MAX_TEXT = 1800

# 与 daily_run.ps1 / daily_task.ps1 的退出码约定一致
_EXIT_MEANING = {
    0: "完成",
    1: "失败（同步不完整、扫描失败或身份校验未通过，详见日志）",
    2: "未启动（入口脚本缺失或参数错误）",
}


class NotifyConfigError(ValueError):
    """推送配置不安全或不完整。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Webhook 重定向被拒绝", headers, fp)


def read_env_file(path: Path) -> dict[str, str]:
    """只读取推送相关键；不写 os.environ。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _ENV_KEYS:
            values[key] = value.strip().strip('"').strip("'")
    return values


def load_config(env: Mapping[str, str]) -> dict[str, Any] | None:
    url = (env.get("AB_NOTIFY_WEBHOOK_URL") or "").strip()
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise NotifyConfigError("AB_NOTIFY_WEBHOOK_URL 必须是 https:// 地址")
    if parsed.username or parsed.password:
        raise NotifyConfigError("AB_NOTIFY_WEBHOOK_URL 不得内嵌账号密码")
    kind = (env.get("AB_NOTIFY_WEBHOOK_KIND") or "generic").strip().lower()
    if kind not in KINDS:
        raise NotifyConfigError(f"AB_NOTIFY_WEBHOOK_KIND 必须是 {'/'.join(KINDS)} 之一")
    on_success = (env.get("AB_NOTIFY_ON_SUCCESS") or "").strip().lower() in {"1", "true", "yes"}
    return {"url": url, "kind": kind, "on_success": on_success}


def _url_secrets(url: str) -> tuple[str, ...]:
    """Webhook 的凭据在查询参数（企业微信 key / 钉钉 access_token）或路径末段（飞书 hook id）。"""
    parsed = urlsplit(url)
    secrets = [url]
    secrets.extend(value for _key, value in parse_qsl(parsed.query) if len(value) >= 6)
    last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if len(last) >= 16:
        secrets.append(last)
    return tuple(secrets)


def _tail(log_path: Path | None, lines: int = TAIL_LINES) -> list[str]:
    if log_path is None or not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in text if line.strip()][-lines:]


def build_message(
    exit_code: int,
    *,
    log_path: Path | None,
    host: str,
    known_secrets: tuple[str, ...] = (),
) -> tuple[str, str]:
    title = "✅ AB 日跑完成" if exit_code == 0 else f"❌ AB 日跑失败（退出码 {exit_code}）"
    if host:
        title += f" · {host}"
    body = [
        f"退出码：{exit_code}（{_EXIT_MEANING.get(exit_code, '未知退出码')}）",
        f"日志：{log_path}" if log_path else "日志：未提供",
    ]
    tail = _tail(log_path)
    if tail:
        body.append("—— 日志末尾 ——")
        body.extend(tail)
    text = redact_sensitive_text("\n".join(body), known_secrets=known_secrets)
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"
    return title, text


def payload_for(kind: str, title: str, text: str) -> dict[str, Any]:
    content = f"{title}\n{text}"
    if kind in ("wecom", "dingtalk"):
        return {"msgtype": "text", "text": {"content": content}}
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": content}}
    return {"title": title, "text": text}


def _post(url: str, payload: dict[str, Any], timeout: float) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return int(response.status)


def notify(
    exit_code: int,
    *,
    env: Mapping[str, str],
    log_path: Path | None = None,
    host: str = "",
    sender: Callable[[str, dict[str, Any], float], int] = _post,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """发送一次推送；返回结果摘要（从不抛出网络异常）。"""
    try:
        config = load_config(env)
    except NotifyConfigError as exc:
        return {"status": "CONFIG_ERROR", "error": str(exc)}
    if config is None:
        return {"status": "NOT_CONFIGURED"}
    if exit_code == 0 and not config["on_success"]:
        return {"status": "SKIPPED_SUCCESS"}
    secrets = tuple(
        s for s in (env.get("TUSHARE_TOKEN", ""), *_url_secrets(config["url"])) if s
    )
    title, text = build_message(exit_code, log_path=log_path, host=host, known_secrets=secrets)
    try:
        code = sender(config["url"], payload_for(config["kind"], title, text), timeout)
    except Exception as exc:  # noqa: BLE001 - 推送失败不能影响日跑
        return {
            "status": "SEND_FAILED",
            "error": redact_sensitive_text(f"{type(exc).__name__}: {exc}", known_secrets=secrets),
        }
    return {"status": "SENT" if 200 <= code < 300 else "SEND_FAILED", "http_status": code}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日跑结果 Webhook 推送（未配置则跳过）")
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--log")
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    args = parser.parse_args(argv)

    env = read_env_file(Path(args.env_file))
    for key in _ENV_KEYS:  # 进程环境只补缺，项目 .env 优先
        if key not in env and os.environ.get(key):
            env[key] = os.environ[key]
    result = notify(
        args.exit_code,
        env=env,
        log_path=Path(args.log) if args.log else None,
        host=os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
    )
    print(f"[notify] {result['status']}" + (f": {result['error']}" if result.get("error") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
