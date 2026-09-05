"""Shared, standard-library-only Python 3.12 bootstrap for local launchers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


class RuntimeSetupError(RuntimeError):
    """An actionable setup failure; never use an unverified runtime."""


def _probe(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            [*command, "-I", "-c", "import json,sys; print(json.dumps([list(sys.version_info[:2]),sys.executable]))"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        version, executable = json.loads(result.stdout.strip())
        if result.returncode == 0 and version == [3, 12] and isinstance(executable, str):
            return executable
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    return None


def _base_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    if os.environ.get("AB_PYTHON"):
        candidates.append([os.environ["AB_PYTHON"]])
    if sys.executable:
        candidates.append([sys.executable])
    if shutil.which("py"):
        candidates.append(["py", "-3.12"])
    for name in ("python3.12", "python", "python3"):
        executable = shutil.which(name)
        if executable:
            candidates.append([executable])
    if os.name == "nt":
        candidates.append([r"C:\Python312\python.exe"])
        if os.environ.get("LOCALAPPDATA"):
            candidates.append([str(Path(os.environ["LOCALAPPDATA"]) / "Programs/Python/Python312/python.exe")])
    return candidates


def project_python(root: Path, *, create: bool = True) -> str:
    """Reuse .venv312, or create it from a probed 3.12; never install globally."""
    environment = root / ".venv312"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if executable.is_file():
        selected = _probe([str(executable)])
        if selected:
            return selected
        raise RuntimeSetupError("项目 .venv312 无法运行或不是 Python 3.12；请保留旧目录并人工修复，未自动覆盖。")
    if environment.exists():
        raise RuntimeSetupError("项目 .venv312 不完整；请保留旧目录并人工修复，未自动覆盖。")
    selected = next((found for command in _base_candidates() if (found := _probe(command))), None)
    if not selected:
        raise RuntimeSetupError(
            "未找到 Python 3.12。请安装 Python 3.12 后重新双击一键启动；"
            "无需卸载 3.14，本程序不会向 3.14 或系统 Python 安装依赖。"
        )
    if not create:
        return selected
    print("[环境] 首次创建项目独立环境 .venv312（Python 3.12）…", flush=True)
    result = subprocess.run([selected, "-m", "venv", str(environment)], check=False)
    if result.returncode != 0 or not (ready := _probe([str(executable)])):
        raise RuntimeSetupError("创建 .venv312 失败；请检查 Python 3.12 的 venv 组件和目录写入权限。")
    return ready


_CHECK_REQUIREMENTS = """
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from packaging.requirements import Requirement
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    raw = line.split('#', 1)[0].strip()
    if not raw:
        continue
    req = Requirement(raw)
    if req.marker and not req.marker.evaluate():
        continue
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        raise SystemExit(1)
    if not req.specifier.contains(installed):
        raise SystemExit(1)
"""


def ensure_dependencies(python: str, root: Path) -> None:
    """Check locally first; explicit beta pin avoids globally enabling --pre."""
    if not _probe([python]):
        raise RuntimeSetupError("依赖安装已阻止：仅支持已验收的 Python 3.12。")
    requirements = root / "requirements.txt"
    checked = subprocess.run(
        [python, "-I", "-c", _CHECK_REQUIREMENTS, str(requirements)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if checked.returncode == 0:
        print("[依赖] 本地依赖已满足，无需联网重装", flush=True)
    else:
        print("[依赖] 安装缺失依赖（使用项目环境，不修改系统 Python）…", flush=True)
        command = [python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)]
        lock = root / "requirements-lock-py312.txt"
        if lock.is_file():
            command.extend(["-c", str(lock)])
        if subprocess.run(command, cwd=str(root), check=False).returncode != 0:
            raise RuntimeSetupError("依赖安装失败。请保留上方具体错误；可能是版本、软件源或网络问题，并非一律网络故障。")
    if subprocess.run([python, "-m", "pip", "check"], cwd=str(root), check=False).returncode != 0:
        raise RuntimeSetupError("依赖版本冲突；尚未启动服务，请先修复上方 pip check 报错。")


if __name__ == "__main__":
    try:
        print(project_python(Path(__file__).resolve().parent))
    except RuntimeSetupError as exc:
        print(f"[环境错误] {exc}", file=sys.stderr)
        raise SystemExit(1) from None
